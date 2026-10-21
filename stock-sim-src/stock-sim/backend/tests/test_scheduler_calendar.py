"""시장 캘린더와 스케줄러 (SPEC 7.1, 12). 네트워크/실제 작업 실행 없음."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import JobRun, MarketCalendar
from app.pipelines import collect_prices, market_calendar, scheduler
from app.services.market_data import KST


class CalSource:
    def __init__(self, open_days):
        self.open_days, self.calls = open_days, []

    def trading_days(self, start, end):
        self.calls.append((start, end))
        return self.open_days


# ---------- 캘린더 ----------
def test_refresh_marks_open_and_closed_days_idempotently(sf):
    src = CalSource([date(2026, 10, 2), date(2026, 10, 5)])
    n = market_calendar.refresh(date(2026, 10, 1), date(2026, 10, 6), source=src, session_factory=sf)
    assert n == 6 and src.calls == [("20261001", "20261006")]
    with sf() as s:
        rows = {r.date: r.is_open for r in s.scalars(select(MarketCalendar))}
    assert rows[date(2026, 10, 1)] is False  # 휴장(주말이 아니어도)
    assert rows[date(2026, 10, 2)] is True and rows[date(2026, 10, 3)] is False  # 토요일
    market_calendar.refresh(date(2026, 10, 1), date(2026, 10, 6), source=src, session_factory=sf)
    assert market_calendar.known_dates(sf) == 6


def test_is_trading_day_uses_calendar_then_weekday(sf):
    market_calendar.refresh(
        date(2026, 10, 1), date(2026, 10, 2), source=CalSource([date(2026, 10, 2)]), session_factory=sf
    )
    assert market_calendar.is_trading_day(date(2026, 10, 1), sf) is False  # 캘린더가 평일 휴장 알려 줌
    assert market_calendar.is_trading_day(date(2026, 10, 2), sf) is True
    assert market_calendar.is_trading_day(date(2030, 1, 7), sf) is True  # 행이 없으면 KRX 휴장일 예측 (월)
    assert market_calendar.is_trading_day(date(2030, 1, 5), sf) is False  # 토


def test_first_trading_day_of_month_skips_holidays(sf):
    src = CalSource([date(2026, 10, 2), date(2026, 10, 5)])  # 10/1 휴장, 10/2 가 첫 거래일
    market_calendar.refresh(date(2026, 10, 1), date(2026, 10, 6), source=src, session_factory=sf)
    f = market_calendar.is_first_trading_day_of_month
    assert f(date(2026, 10, 1), sf) is False and f(date(2026, 10, 2), sf) is True
    assert f(date(2026, 10, 5), sf) is False and f(date(2026, 10, 3), sf) is False


# ---------- 실행 주기 판단 ----------
def dt(h, m, day=21):  # 2026-10-21 은 수요일
    return datetime(2026, 10, day, h, m, tzinfo=KST)


def test_open_window_is_weekday_0830_to_1600():
    assert scheduler.in_open_window(dt(8, 30)) and scheduler.in_open_window(dt(15, 59))
    assert not scheduler.in_open_window(dt(8, 29)) and not scheduler.in_open_window(dt(16, 0))
    assert not scheduler.in_open_window(dt(10, 0, day=24))  # 토요일


@pytest.mark.parametrize(
    "now,ago_min,due",
    [
        (dt(10, 0), 2, False), (dt(10, 0), 5, True), (dt(10, 0), 6, True),   # 장중 5분
        (dt(20, 0), 20, False), (dt(20, 0), 30, True), (dt(20, 0), 31, True),  # 장외 30분
        (dt(10, 0, day=24), 10, False),  # 주말은 장외 취급
    ],
)  # fmt: skip
def test_is_due_news_intervals(now, ago_min, due):
    assert scheduler.is_due(now, now - timedelta(minutes=ago_min), 5, 30) is due


def test_is_due_first_run_and_disclosure_intervals():
    assert scheduler.is_due(dt(10, 0), None, 10, 60)
    assert not scheduler.is_due(dt(10, 0), dt(10, 0) - timedelta(minutes=9), 10, 60)
    assert scheduler.is_due(dt(10, 0), dt(10, 0) - timedelta(minutes=10), 10, 60)
    assert not scheduler.is_due(dt(22, 0), dt(22, 0) - timedelta(minutes=59), 10, 60)


def test_news_job_respects_interval_and_credentials(sf, monkeypatch):
    runs = []
    monkeypatch.setattr(scheduler.collect_news, "run", lambda **kw: runs.append(1))
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    scheduler.news_job(now=dt(10, 0), session_factory=sf)
    assert runs == []  # 키가 없으면 건너뜀 (job_runs 에 실패가 쌓이지 않는다)
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    scheduler.news_job(now=dt(10, 0), session_factory=sf)
    assert runs == [1]
    with sf() as s:  # 방금 시작한 것으로 기록 -> 2분 뒤에는 아직 아님
        s.add(JobRun(job="collect_news", status="success", started_at=dt(10, 0)))
        s.commit()
    scheduler.news_job(now=dt(10, 2), session_factory=sf)
    assert runs == [1]
    scheduler.news_job(now=dt(10, 6), session_factory=sf)
    assert runs == [1, 1]


def test_guarded_skips_missing_env_and_swallows_errors(monkeypatch):
    called = []
    monkeypatch.delenv("SOME_KEY", raising=False)
    scheduler._guarded("j", lambda: called.append(1), ("SOME_KEY",))
    assert called == []

    def boom():
        raise RuntimeError("x")

    scheduler._guarded("j", boom)  # 예외가 스케줄러를 죽이지 않는다


# ---------- 일일 작업 ----------
def test_post_close_skips_holidays(sf, monkeypatch):
    market_calendar.refresh(date(2026, 10, 1), date(2026, 10, 1), source=CalSource([]), session_factory=sf)
    ran = []
    monkeypatch.setattr(collect_prices, "run", lambda **kw: ran.append("prices"))
    monkeypatch.setattr(collect_prices, "select_universe", lambda *a, **kw: ran.append("universe"))
    scheduler.post_close(sf, today=date(2026, 10, 1))
    assert ran == []


def test_post_close_runs_universe_only_on_first_trading_day(sf, monkeypatch):
    src = CalSource([date(2026, 10, 2), date(2026, 10, 5)])
    market_calendar.refresh(date(2026, 10, 1), date(2026, 10, 6), source=src, session_factory=sf)
    ran = []
    monkeypatch.setattr(collect_prices, "run", lambda **kw: ran.append("prices"))
    monkeypatch.setattr(collect_prices, "select_universe", lambda *a, **kw: ran.append("universe"))
    scheduler.post_close(sf, today=date(2026, 10, 2))
    assert ran == ["universe", "prices"]  # 유니버스를 먼저 바꾼 뒤 가격 수집
    ran.clear()
    scheduler.post_close(sf, today=date(2026, 10, 5))
    assert ran == ["prices"]


def test_build_scheduler_registers_spec_jobs(sf):
    sched = scheduler.build_scheduler(sf)
    jobs = {j.id: j for j in sched.get_jobs()}
    assert set(jobs) == {"post_close", "macro", "news", "disclosures", "weekly"}
    assert str(sched.timezone) == "Asia/Seoul"
    fields = lambda j: {f.name: str(f) for f in j.trigger.fields}  # noqa: E731
    pc = fields(jobs["post_close"])
    assert (pc["day_of_week"], pc["hour"], pc["minute"]) == ("mon-fri", "15", "40")  # 평일 15:40
    wk = fields(jobs["weekly"])
    assert (wk["day_of_week"], wk["hour"], wk["minute"]) == ("sun", "3", "0")  # 매주 일 03:00
    assert jobs["news"].trigger.interval == timedelta(minutes=5)
    assert jobs["disclosures"].trigger.interval == timedelta(minutes=10)
    assert all(j.max_instances == 1 for j in jobs.values())  # 같은 작업 동시 실행 금지


# ---------- 미래 휴장일 예측 (KRX 캘린더) ----------
@pytest.mark.parametrize(
    "d,is_open",
    [
        (date(2026, 10, 22), True),   # 평일
        (date(2026, 12, 25), False),  # 성탄절
        (date(2026, 12, 31), False),  # 연말 휴장
        (date(2027, 2, 8), False),    # 설 연휴
        (date(2026, 5, 1), False),    # 근로자의날
        (date(2026, 10, 24), False),  # 토요일
    ],
)  # fmt: skip
def test_future_days_without_calendar_rows_use_krx_holidays(sf, d, is_open):
    assert market_calendar.is_trading_day(d, sf) is is_open


def test_market_state_is_closed_on_holidays():
    from app.services.market_hours import market_state

    assert market_state(datetime(2026, 12, 25, 10, 0, tzinfo=KST)) == "closed"  # 평일이지만 휴장
    assert market_state(datetime(2026, 10, 22, 10, 0, tzinfo=KST)) == "open"
    assert market_state(datetime(2026, 10, 22, 15, 30, tzinfo=KST)) == "closed"  # 15:30 마감
    assert market_state(datetime(2026, 12, 25, 10, 0, tzinfo=KST), trading_day=True) == "open"  # 캘린더 우선
