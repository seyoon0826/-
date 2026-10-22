"""공시/거시 지표 수집 (FR-CL-1/6). DART/ECOS 는 가짜 함수로 대체."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models import Disclosure, MacroDaily, Stock
from app.pipelines import collect_disclosures as cd
from app.pipelines import collect_macro as cm
from app.services.market_data import KST

END = date(2026, 10, 20)


def _count(sf, model):
    with sf() as s:
        return s.scalar(select(func.count()).select_from(model))


# ---------- 거시 ----------
def test_parse_rows_skips_bad_values():
    rows = [
        {"TIME": "20261019", "DATA_VALUE": "2.50"},
        {"TIME": "20261020", "DATA_VALUE": "1,385.2"},
        {"TIME": "20261021", "DATA_VALUE": "-"},
        {"TIME": "bad", "DATA_VALUE": "1"},
        {"DATA_VALUE": "1"},
    ]
    assert cm.parse_rows(rows) == {date(2026, 10, 19): Decimal("2.50"), date(2026, 10, 20): Decimal("1385.2")}


def test_macro_run_idempotent_and_updates(sf):
    calls = []

    def usd(start, end):
        calls.append((start, end))
        return [{"TIME": "20261019", "DATA_VALUE": "1380.5"}, {"TIME": "20261020", "DATA_VALUE": "1385.2"}]

    fetchers = {"usdkrw": usd, "base_rate": lambda s, e: [{"TIME": "20261020", "DATA_VALUE": "2.5"}]}
    res = cm.run(fetchers=fetchers, end=END, session_factory=sf)
    assert res.ok == 2 and res.failed == []
    assert _count(sf, MacroDaily) == 3
    assert calls[0] == ("20180101", "20181231") and calls[-1][1] == "20261020"  # 연 단위 조회
    assert len(calls) == 9  # 2018~2026
    cm.run(fetchers=fetchers, end=END, session_factory=sf)
    assert _count(sf, MacroDaily) == 3
    fetchers["usdkrw"] = lambda s, e: [{"TIME": "20261020", "DATA_VALUE": "1390.0"}]
    cm.run(fetchers=fetchers, end=END, session_factory=sf)
    with sf() as s:
        assert s.get(MacroDaily, ("usdkrw", END)).value == Decimal("1390.000000")


def test_macro_incremental_starts_from_last_date(sf):
    seen = []
    fetchers = {"usdkrw": lambda s, e: seen.append((s, e)) or [{"TIME": "20261020", "DATA_VALUE": "1"}]}
    cm.run(fetchers=fetchers, end=END, session_factory=sf)
    seen.clear()
    cm.run(fetchers=fetchers, end=END, session_factory=sf)
    assert seen == [("20261020", "20261020")]


def test_macro_one_series_failure_is_isolated(sf):
    def boom(s, e):
        raise RuntimeError("x")

    ok = lambda s, e: [{"TIME": "20261020", "DATA_VALUE": "1"}]  # noqa: E731
    res = cm.run(fetchers={"base_rate": boom, "usdkrw": ok}, end=END, session_factory=sf)
    assert res.failed == ["base_rate: RuntimeError"] and res.ok == 1 and _count(sf, MacroDaily) == 1


# ---------- 공시 ----------
@pytest.fixture
def stocks(sf):
    with sf() as s:
        s.add_all([Stock(code="005930", name="삼성전자"), Stock(code="000660", name="SK하이닉스")])
        s.commit()


def dart_item(no, dt="20261020", nm="분기보고서 (2026.09)"):
    return {"rcept_no": no, "corp_code": "00126380", "report_nm": nm, "rcept_dt": dt}


def test_corp_map_is_cached_for_a_week(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_dir", str(tmp_path))
    calls = []

    def loader():
        calls.append(1)
        return {"005930": "00126380"}

    now = datetime(2026, 10, 20, tzinfo=UTC)
    assert cd.load_corp_map(loader, now=now) == {"005930": "00126380"}
    assert cd.load_corp_map(loader, now=now + timedelta(days=6)) == {"005930": "00126380"}
    assert len(calls) == 1  # 7일 이내에는 캐시
    cd.load_corp_map(loader, now=now + timedelta(days=8))
    assert len(calls) == 2
    cd.load_corp_map(loader, now=now + timedelta(days=8), force=True)
    assert len(calls) == 3
    assert "fetched_at" in json.loads((tmp_path / "dart_corp_codes.json").read_text())


def test_disclosures_run_stores_rows_idempotently(sf, stocks):
    def list_fn(corp, start, end):
        return [
            dart_item("20261020000001"),
            dart_item("20261019000009", "20261019", "주요사항보고서(자기주식취득)"),
        ]

    cmap = {"005930": "00126380", "000660": "00164779"}
    res = cd.run(list_fn=list_fn, end=END, session_factory=sf, corp_map=cmap)
    assert res.ok == 2 and res.failed == []
    assert _count(sf, Disclosure) == 2  # rcept_no 가 같으면 종목별 중복 저장 안 함
    cd.run(list_fn=list_fn, end=END, session_factory=sf, corp_map=cmap)
    assert _count(sf, Disclosure) == 2
    with sf() as s:
        d = s.get(Disclosure, "20261019000009")
    assert d.report_type == "주요사항보고서" and d.code in ("005930", "000660")


def test_disclosure_filed_at_is_end_of_day_kst_to_avoid_leakage():
    f = cd.to_filed_at("20261020")
    assert f.astimezone(KST).strftime("%Y-%m-%d %H:%M") == "2026-10-20 23:59"  # 장 마감 후 = 다음 거래일 피처


def test_disclosures_incremental_window_and_backfill(sf, stocks):
    seen = []

    def list_fn(corp, start, end):
        seen.append((corp, start, end))
        return [dart_item("20261020000001" if corp == "00126380" else "20261020000002")]

    cmap = {"005930": "00126380", "000660": "00164779"}
    cd.run(list_fn=list_fn, end=END, session_factory=sf, corp_map=cmap)
    assert seen[0][1] == "20180101"  # 최초 백필
    seen.clear()
    cd.run(list_fn=list_fn, end=END, session_factory=sf, corp_map=cmap)
    assert seen[0][1] == "20261019"  # 마지막 공시일 하루 전부터 (겹쳐 받아도 멱등)


def test_missing_corp_code_and_api_error_are_isolated(sf, stocks):
    def list_fn(corp, start, end):
        if corp == "00126380":
            raise RuntimeError("020")
        return [dart_item("1")]

    res = cd.run(list_fn=list_fn, end=END, session_factory=sf, corp_map={"005930": "00126380"})
    assert sorted(res.failed) == ["000660: corp_code 없음", "005930: RuntimeError"] and res.ok == 0
    res = cd.run(
        list_fn=lambda *a: [dart_item("2")], end=END, session_factory=sf, corp_map={"005930": "00126380"}
    )
    assert res.ok == 1 and res.failed == ["000660: corp_code 없음"]
