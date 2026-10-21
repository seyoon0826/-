"""APScheduler 진입점. python -m app.pipelines.scheduler  (SPEC 12)

| 시각 (KST, 거래일)       | 작업                                                              |
| 평일 15:40               | (매월 첫 거래일이면 유니버스 재선정) -> 가격/수급/지수 수집        |
| 평일 16:10               | ECOS 거시 지표                                                    |
| 뉴스: 장중 5분 / 장외 30분 | 5분마다 깨어나 마지막 실행 시각으로 실행 여부 판단                 |
| 공시: 장중 10분 / 장외 60분 | 10분마다 깨어나 같은 방식으로 판단                                |
| 매주 일 03:00            | DART corp_code 갱신, market_calendar 갱신                         |
시작 시 한 번 catch_up() 으로 누락분을 채운다 (모든 수집은 증분/멱등이라 안전하다).
예측 일일 배치(daily_batch)는 Phase 6 에서 15:40 작업 뒤에 이어 붙인다.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import date, datetime, time, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from app.core.config import settings
from app.core.db import SessionLocal
from app.pipelines import collect_disclosures, collect_macro, collect_news, collect_prices, market_calendar
from app.pipelines.common import SessionFactory, last_started_at
from app.services.market_data import KST

log = logging.getLogger(__name__)

NEWS_OPEN, NEWS_CLOSE = time(8, 30), time(16, 0)  # 이 시간대는 '장중' 주기를 쓴다
CALENDAR_START = date(2018, 1, 1)


def in_open_window(now: datetime) -> bool:
    local = now.astimezone(KST)
    return local.weekday() < 5 and NEWS_OPEN <= local.time() < NEWS_CLOSE


def is_due(now: datetime, last_started: datetime | None, open_min: int, closed_min: int) -> bool:
    """마지막 시작 이후 (장중 open_min / 장외 closed_min) 분이 지났으면 실행한다."""
    if last_started is None:
        return True
    interval = timedelta(minutes=open_min if in_open_window(now) else closed_min)
    return now - last_started >= interval - timedelta(seconds=5)  # 스케줄 지터 허용


def missing_env(*names: str) -> list[str]:
    return [n for n in names if not os.getenv(n)]


def _guarded(job: str, fn: Callable[[], object], env: tuple[str, ...] = ()) -> None:
    """키가 없으면 경고만 하고 건너뛴다(실패 기록이 주기마다 쌓이지 않게). 예외는 삼켜 스케줄러를 보호한다."""
    if absent := missing_env(*env):
        log.warning("%s 건너뜀: %s 미설정", job, ", ".join(absent))
        return
    try:
        fn()
    except Exception as e:  # job_run 이 이미 job_runs 에 기록했다
        log.error("%s 실패: %s", job, e.__class__.__name__)


def post_close(session_factory: SessionFactory = SessionLocal, today: date | None = None) -> None:
    today = today or datetime.now(KST).date()
    if not market_calendar.is_trading_day(today, session_factory):
        log.info("휴장일: post_close 건너뜀")
        return
    if market_calendar.is_first_trading_day_of_month(today, session_factory):
        _guarded(
            "select_universe", lambda: collect_prices.select_universe(today, session_factory=session_factory)
        )
    _guarded("collect_prices", lambda: collect_prices.run(session_factory=session_factory))


def macro_job(session_factory: SessionFactory = SessionLocal) -> None:
    _guarded("collect_macro", lambda: collect_macro.run(session_factory=session_factory), ("ECOS_API_KEY",))


def news_job(now: datetime | None = None, session_factory: SessionFactory = SessionLocal) -> None:
    now = now or datetime.now(KST)
    last = last_started_at("collect_news", session_factory)
    if is_due(now, last, settings.news_interval_open_min, settings.news_interval_closed_min):
        creds = ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")
        _guarded("collect_news", lambda: collect_news.run(session_factory=session_factory), creds)


def disclosure_job(now: datetime | None = None, session_factory: SessionFactory = SessionLocal) -> None:
    now = now or datetime.now(KST)
    last = last_started_at("collect_disclosures", session_factory)
    if is_due(now, last, settings.disclosure_interval_open_min, settings.disclosure_interval_closed_min):
        _guarded(
            "collect_disclosures",
            lambda: collect_disclosures.run(session_factory=session_factory),
            ("DART_API_KEY",),
        )


def weekly_job(session_factory: SessionFactory = SessionLocal, today: date | None = None) -> None:
    today = today or datetime.now(KST).date()
    _guarded(
        "refresh_corp_codes",
        lambda: collect_disclosures.refresh_corp_codes(session_factory=session_factory),
        ("DART_API_KEY",),
    )
    start = (
        CALENDAR_START if market_calendar.known_dates(session_factory) == 0 else today - timedelta(days=30)
    )
    _guarded(
        "refresh_calendar", lambda: market_calendar.refresh(start, today, session_factory=session_factory)
    )


def catch_up(session_factory: SessionFactory = SessionLocal) -> None:
    """시작 시 누락분 보충: 캘린더 -> (유니버스) -> 가격 -> 거시 -> 공시 -> 뉴스."""
    today = datetime.now(KST).date()
    if market_calendar.known_dates(session_factory) == 0:
        _guarded(
            "refresh_calendar",
            lambda: market_calendar.refresh(CALENDAR_START, today, session_factory=session_factory),
        )
    _guarded("collect_prices", lambda: collect_prices.run(session_factory=session_factory))
    macro_job(session_factory)
    disclosure_job(session_factory=session_factory)
    news_job(session_factory=session_factory)


def build_scheduler(session_factory: SessionFactory = SessionLocal) -> BlockingScheduler:
    sched = BlockingScheduler(timezone="Asia/Seoul")
    common = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600}
    sf = {"session_factory": session_factory}
    sched.add_job(
        post_close, "cron", id="post_close", day_of_week="mon-fri", hour=15, minute=40, kwargs=sf, **common
    )
    sched.add_job(
        macro_job, "cron", id="macro", day_of_week="mon-fri", hour=16, minute=10, kwargs=sf, **common
    )
    sched.add_job(
        news_job, "interval", id="news", minutes=settings.news_interval_open_min, kwargs=sf, **common
    )
    sched.add_job(
        disclosure_job,
        "interval",
        id="disclosures",
        minutes=settings.disclosure_interval_open_min,
        kwargs=sf,
        **common,
    )
    sched.add_job(weekly_job, "cron", id="weekly", day_of_week="sun", hour=3, minute=0, kwargs=sf, **common)
    return sched


def main() -> None:  # pragma: no cover (프로세스 진입점)
    from dotenv import load_dotenv

    load_dotenv("../.env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sched = build_scheduler()
    catch_up()
    sched.start()


if __name__ == "__main__":  # pragma: no cover
    main()
