"""가격/수급/지수 수집과 유니버스 선정 (FR-CL-1/2/5/6, FR-ST-1). pykrx 는 가짜 source 로 대체."""

from datetime import date

import pandas as pd
from sqlalchemy import func, select

from app.models import IndexDaily, JobRun, PriceDaily, Stock
from app.pipelines import collect_prices

END = date(2026, 10, 20)


def frame(rows):
    """rows: [(date, close)] -> collect_full 형태의 DataFrame."""
    idx = [d for d, _ in rows]
    return pd.DataFrame(
        {
            "open": [c - 100 for _, c in rows],
            "high": [c + 200 for _, c in rows],
            "low": [c - 300 for _, c in rows],
            "close": [c for _, c in rows],
            "volume": [1_000] * len(rows),
            "value": [70_000_000] * len(rows),
            "market_cap": [400_000_000_000_000] * len(rows),
            "foreign_net": [5_000] * len(rows),
            "inst_net": [-3_000] * len(rows),
        },
        index=idx,
    )


class FakeSource:
    def __init__(self, data=None, fail=(), top=None):
        self.data, self.fail, self.top = data or {}, set(fail), top
        self.full_calls, self.index_calls, self.top_calls = [], [], []

    def latest_business_day(self, before=None):
        return (before or END).strftime("%Y%m%d")

    def top_marketcap(self, n, date, include_preferred):
        self.top_calls.append((n, date, include_preferred))
        return self.top

    def collect_full(self, code, start, end):
        self.full_calls.append((code, start, end))
        if code in self.fail:
            raise RuntimeError("KRX 장애")
        return self.data.get(code, frame([]))

    def index_close(self, ticker, start, end):
        self.index_calls.append((ticker, start, end))
        return pd.Series({date(2026, 10, 19): 4400.5, date(2026, 10, 20): 4412.25})


def _stocks(sf, *codes, active=True):
    with sf() as s:
        for c in codes:
            s.add(Stock(code=c, name=f"종목{c}", active=active))
        s.commit()


def _count(sf, model):
    with sf() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_first_run_backfills_from_2018_FR_CL_2(sf):
    _stocks(sf, "005930")
    src = FakeSource({"005930": frame([(date(2026, 10, 19), 71_200), (END, 72_100)])})
    res = collect_prices.run(end=END, source=src, session_factory=sf)
    assert src.full_calls == [("005930", "20180101", "20261020")]
    assert src.index_calls[0][1] == "20180101"
    assert (res.ok, res.failed) == (2, [])  # 종목 1 + 지수 1
    assert _count(sf, PriceDaily) == 2 and _count(sf, IndexDaily) == 2


def test_rerun_is_idempotent_and_incremental_FR_CL_1(sf):
    _stocks(sf, "005930")
    data = {"005930": frame([(date(2026, 10, 19), 71_200), (END, 72_100)])}
    collect_prices.run(end=END, source=FakeSource(data), session_factory=sf)
    src2 = FakeSource(data)
    collect_prices.run(end=END, source=src2, session_factory=sf)
    assert _count(sf, PriceDaily) == 2 and _count(sf, IndexDaily) == 2  # 중복 행 없음
    assert src2.full_calls == [("005930", "20261020", "20261020")]  # 마지막 저장일부터 다시 받는다
    assert src2.index_calls[0][1] == "20261020"


def test_rerun_overwrites_revised_values(sf):
    _stocks(sf, "005930")
    collect_prices.run(end=END, source=FakeSource({"005930": frame([(END, 72_100)])}), session_factory=sf)
    collect_prices.run(end=END, source=FakeSource({"005930": frame([(END, 72_500)])}), session_factory=sf)
    with sf() as s:
        assert s.get(PriceDaily, ("005930", END)).close == 72_500


def test_all_columns_stored(sf):
    _stocks(sf, "005930")
    collect_prices.run(end=END, source=FakeSource({"005930": frame([(END, 72_100)])}), session_factory=sf)
    with sf() as s:
        r = s.get(PriceDaily, ("005930", END))
    assert (r.open, r.high, r.low, r.close, r.volume) == (72_000, 72_300, 71_800, 72_100, 1_000)
    assert (r.value, r.foreign_net, r.inst_net, r.market_cap) == (
        70_000_000,
        5_000,
        -3_000,
        400_000_000_000_000,
    )


def test_zero_close_rows_skipped_and_nan_becomes_null(sf):
    _stocks(sf, "005930")
    df = frame([(date(2026, 10, 19), 0), (END, 72_100)])
    df.loc[END, "foreign_net"] = float("nan")
    collect_prices.run(end=END, source=FakeSource({"005930": df}), session_factory=sf)
    with sf() as s:
        assert s.get(PriceDaily, ("005930", date(2026, 10, 19))) is None  # 거래정지/휴장 행
        assert s.get(PriceDaily, ("005930", END)).foreign_net is None


def test_partial_failure_continues_and_is_recorded_FR_CL_5_6(sf):
    _stocks(sf, "000660", "005930", "035420")
    data = {c: frame([(END, 70_000)]) for c in ("000660", "005930", "035420")}
    res = collect_prices.run(end=END, source=FakeSource(data, fail={"005930"}), session_factory=sf)
    assert res.failed == ["005930: RuntimeError"] and res.ok == 3  # 2종목 + 지수
    with sf() as s:
        codes = set(s.scalars(select(PriceDaily.code)))
        job = s.scalars(select(JobRun).where(JobRun.job == "collect_prices")).one()
    assert codes == {"000660", "035420"}  # 실패 종목만 빠지고 나머지는 계속 진행
    assert (job.status, job.items_ok, job.items_failed) == ("partial", 3, 1)
    assert "005930" in job.error


def test_index_failure_is_isolated(sf):
    _stocks(sf, "005930")

    class BadIndex(FakeSource):
        def index_close(self, *a):
            raise RuntimeError("x")

    res = collect_prices.run(end=END, source=BadIndex({"005930": frame([(END, 1)])}), session_factory=sf)
    assert res.failed == ["KOSPI: RuntimeError"] and _count(sf, PriceDaily) == 1


def test_up_to_date_code_is_skipped_without_fetch(sf):
    _stocks(sf, "005930")
    collect_prices.run(end=END, source=FakeSource({"005930": frame([(END, 72_100)])}), session_factory=sf)
    src = FakeSource()
    collect_prices.run(end=date(2026, 10, 19), source=src, session_factory=sf)  # 마지막 저장일이 end 보다 뒤
    assert src.full_calls == [] and src.index_calls == []


# ---------- 유니버스 (FR-ST-1) ----------
def top(*pairs):
    return pd.DataFrame(
        {"code": [c for c, _ in pairs], "name": [n for _, n in pairs], "시가총액": range(len(pairs), 0, -1)}
    )


def test_select_universe_activates_top_and_deactivates_rest_but_keeps_data(sf):
    with sf() as s:
        s.add_all(
            [Stock(code="005930", name="삼성전자", aliases=["삼성"]), Stock(code="111111", name="편출될종목")]
        )
        s.flush()
        s.add(PriceDaily(code="111111", date=END, open=1, high=1, low=1, close=1, volume=1))
        s.commit()
    src = FakeSource(top=top(("005930", "삼성전자"), ("000660", "SK하이닉스")))
    codes = collect_prices.select_universe(
        END, size=2, include_preferred=False, source=src, session_factory=sf
    )
    assert codes == ["005930", "000660"]
    assert src.top_calls == [(2, "20261020", False)]
    with sf() as s:
        active = {x.code: x for x in s.scalars(select(Stock))}
        assert active["005930"].active and active["000660"].active and not active["111111"].active
        assert active["005930"].aliases == ["삼성"]  # 별칭은 덮어쓰지 않는다
        assert s.get(PriceDaily, ("111111", END)) is not None  # 편출 종목 데이터는 유지


def test_select_universe_defaults_come_from_settings(sf, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "universe_size", 2)
    monkeypatch.setattr(settings, "universe_include_preferred", True)
    src = FakeSource(top=top(("005930", "삼성전자"), ("005935", "삼성전자우")))
    collect_prices.select_universe(END, source=src, session_factory=sf)
    assert src.top_calls == [(2, "20261020", True)]


def test_run_selects_universe_when_none_active(sf, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "universe_size", 1)
    src = FakeSource({"005930": frame([(END, 72_100)])}, top=top(("005930", "삼성전자")))
    res = collect_prices.run(end=END, source=src, session_factory=sf)
    assert src.top_calls and res.failed == [] and _count(sf, PriceDaily) == 1
    with sf() as s:
        assert {j.job for j in s.scalars(select(JobRun))} == {"select_universe", "collect_prices"}


def test_preferred_share_detection():
    from app.clients.pykrx_client import is_preferred

    assert is_preferred("005935") and not is_preferred("005930") and not is_preferred("000660")


# ---------- 장중 미확정 봉 제외 ----------
def _kst(y, m, d, h, mi):
    from app.services.market_data import KST

    return __import__("datetime").datetime(y, m, d, h, mi, tzinfo=KST)


def test_intraday_today_is_excluded_until_1540():
    class Src(FakeSource):
        def latest_business_day(self, before=None):
            return (before or date(2026, 10, 21)).strftime("%Y%m%d")  # 오늘=10/21(수)이 영업일

    assert collect_prices.final_end_date(Src(), _kst(2026, 10, 21, 13, 30)) == date(2026, 10, 20)  # 장중
    assert collect_prices.final_end_date(Src(), _kst(2026, 10, 21, 15, 39)) == date(2026, 10, 20)
    assert collect_prices.final_end_date(Src(), _kst(2026, 10, 21, 15, 40)) == date(2026, 10, 21)  # 마감 후
    assert collect_prices.final_end_date(Src(), _kst(2026, 10, 22, 9, 0)) == date(
        2026, 10, 21
    )  # 다음 날 아침


def test_run_uses_final_end_date_by_default(sf):
    _stocks(sf, "005930")

    class Src(FakeSource):
        def latest_business_day(self, before=None):
            return (before or date(2026, 10, 21)).strftime("%Y%m%d")

    src = Src({"005930": frame([(date(2026, 10, 20), 72_100)])})
    collect_prices.run(source=src, session_factory=sf, now=_kst(2026, 10, 21, 11, 0))
    assert src.full_calls == [("005930", "20180101", "20261020")]


def test_trading_halt_rows_become_flat_bars(sf):
    """KRX 거래정지일: 시/고/저가와 거래량 0, 종가만 직전가 -> 종가 기준 평평한 봉 (실데이터: HMM 2018-01)."""
    _stocks(sf, "011200")
    df = frame([(END, 4_620)])
    df.loc[END, ["open", "high", "low", "volume"]] = 0
    collect_prices.run(end=END, source=FakeSource({"011200": df}), session_factory=sf)
    with sf() as s:
        r = s.get(PriceDaily, ("011200", END))
    assert (r.open, r.high, r.low, r.close, r.volume) == (4_620, 4_620, 4_620, 4_620, 0)


def test_adjusted_price_rounding_is_clamped(sf):
    """수정주가 반올림으로 종가 > 고가인 경우 (실데이터: 034020 2018-01-02 종가 11,935 / 고가 11,933)."""
    _stocks(sf, "034020")
    df = frame([(END, 11_935)])
    df.loc[END, ["open", "high", "low"]] = [11_703, 11_933, 11_513]
    collect_prices.run(end=END, source=FakeSource({"034020": df}), session_factory=sf)
    with sf() as s:
        r = s.get(PriceDaily, ("034020", END))
    assert (r.open, r.high, r.low, r.close) == (11_703, 11_935, 11_513, 11_935)
    assert r.low <= min(r.open, r.close) <= max(r.open, r.close) <= r.high


def test_halted_day_investor_flow_is_zero_but_traded_day_missing_stays_null(sf):
    """거래정지일(거래량 0)은 순매수가 실제로 0원 -> 0. 거래가 있었는데 값이 빈 날은 진짜 결측 -> NULL.
    (실데이터: 누락 74행이 전부 거래량 0인 거래정지일이었다. 예: 010120 2026-04-08~10)"""
    _stocks(sf, "010120")
    halted, traded = date(2026, 10, 19), END
    df = frame([(halted, 50_000), (traded, 50_500)])
    df.loc[halted, ["open", "high", "low", "volume"]] = 0
    df.loc[[halted, traded], ["foreign_net", "inst_net"]] = float("nan")
    collect_prices.run(end=END, source=FakeSource({"010120": df}), session_factory=sf)
    with sf() as s:
        h, t = s.get(PriceDaily, ("010120", halted)), s.get(PriceDaily, ("010120", traded))
    assert (h.volume, h.foreign_net, h.inst_net) == (0, 0, 0)
    assert (t.volume, t.foreign_net, t.inst_net) == (1_000, None, None)


def test_halted_day_keeps_reported_flow_if_present(sf):
    _stocks(sf, "010120")
    df = frame([(END, 50_000)])
    df.loc[END, ["open", "high", "low", "volume"]] = 0  # frame 의 순매수 값(5,000 / -3,000)은 그대로 둔다
    collect_prices.run(end=END, source=FakeSource({"010120": df}), session_factory=sf)
    with sf() as s:
        r = s.get(PriceDaily, ("010120", END))
    assert (r.foreign_net, r.inst_net) == (5_000, -3_000)


def test_incomplete_top_list_does_not_wipe_universe(sf):
    """KRX 가 빈/불완전 응답을 주면 기존 유니버스를 그대로 둔다 (전 종목 편출 사고 방지)."""
    _stocks(sf, "005930", "000660")
    src = FakeSource(top=top(("005930", "삼성전자")))
    import pytest

    with pytest.raises(RuntimeError, match="1개뿐"):
        collect_prices.select_universe(END, size=30, source=src, session_factory=sf)
    with sf() as s:
        assert all(x.active for x in s.scalars(select(Stock)))
        job = s.scalars(select(JobRun).where(JobRun.job == "select_universe")).one()
    assert job.status == "failed"
