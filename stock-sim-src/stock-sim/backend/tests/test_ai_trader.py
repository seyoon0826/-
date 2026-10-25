"""AI 계정 리밸런싱 (FR-AI-2/3/4): 규칙, 일반 엔진 통과, 근거 예측 기록, 재실행 멱등."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import fakeredis
import pytest
from sqlalchemy import select

from app.models import Order, Prediction, Stock
from app.pipelines import paper_jobs
from app.services.market_data import KST, BookLevel, OrderBook
from app.services.paper import ai_trader as ai
from app.services.paper.engine import MarketSnapshot
from tests.paper_support import OPEN, setup

RULES = ai.Rules(top_n=5, buy_threshold=0.55, sell_threshold=0.50)
VERSION = "test-model"
PRICES = {"005930": 70_000, "000660": 200_000, "035420": 150_000}
SIGNAL_DAY = date(2026, 10, 20)  # OPEN(10-21 수) 직전 거래일
NEXT_DAY = datetime(2026, 10, 22, 9, 5, tzinfo=KST)


@pytest.fixture
def paper(sf):
    p = setup(sf, codes=tuple(PRICES))
    yield p
    assert p.violations() == {}


def snap(prices=PRICES):
    def f(s, code, now):
        px = prices.get(code, 10_000)
        b = OrderBook(code, (BookLevel(px, 100_000),), (BookLevel(px, 100_000),), now, "kis", 1)
        return MarketSnapshot(b, px, now, "kis", px)

    return f


def predict(sf, day: date, p_up: dict[str, float]) -> dict[str, int]:
    ids = {}
    with sf() as s:
        for code, p in p_up.items():
            row = Prediction(
                code=code,
                as_of_date=day,
                horizon=5,
                close_at_asof=PRICES.get(code, 10_000),
                p_up=Decimal(str(p)),
                p_outperform=None,
                q10=Decimal("-0.03"),
                q50=Decimal("0"),
                q90=Decimal("0.03"),
                model_version=VERSION,
            )
            s.add(row)
            s.flush()
            ids[code] = row.id
        s.commit()
    return ids


def run(paper, now=OPEN, prices=PRICES):
    orders, _ = ai.rebalance_account(paper.sf, paper.ai_account_id, snap(prices), now, RULES, VERSION)
    return orders


def ai_orders(paper) -> list[Order]:
    with paper.sf() as s:
        return list(
            s.scalars(select(Order).where(Order.account_id == paper.ai_account_id).order_by(Order.created_at))
        )


# ---------------------------------------------------------------- 규칙 (순수 함수)
def test_plan_sells_below_threshold_and_buys_top_n_above_threshold():
    sig = {
        "A": ai.Signal("A", 0.70, 1),
        "B": ai.Signal("B", 0.56, 2),
        "C": ai.Signal("C", 0.53, 3),  # 매수 기준 미달, 보유 중이면 유지
        "D": ai.Signal("D", 0.45, 4),  # 매도 기준 미달
    }
    holdings = {"C": 10, "D": 7}
    prices = {"A": 1_000, "B": 2_000, "C": 5_000, "D": 3_000}
    p = ai.plan(holdings, prices, total_value=1_000_000, sig=sig, rules=ai.Rules(2, 0.55, 0.50))
    assert [(x.side, x.code, x.qty, x.prediction_id) for x in p] == [
        ("SELL", "D", 7, 4),  # 매도가 먼저
        ("BUY", "A", 500, 1),  # 목표 = 총자산 / N = 500,000
        ("BUY", "B", 250, 2),
    ]


def test_plan_tops_up_only_the_shortfall_and_never_trims():
    sig = {"A": ai.Signal("A", 0.6, 1), "B": ai.Signal("B", 0.6, 2)}
    p = ai.plan({"A": 300, "B": 900}, {"A": 1_000, "B": 1_000}, 1_000_000, sig, ai.Rules(2, 0.55, 0.5))
    assert [(x.code, x.qty) for x in p] == [("A", 200)]  # B 는 목표 초과지만 줄이지 않는다


def test_plan_holds_cash_when_fewer_than_n_qualify():
    sig = {"A": ai.Signal("A", 0.6, 1), "B": ai.Signal("B", 0.54, 2)}
    p = ai.plan({}, {"A": 1_000, "B": 1_000}, 1_000_000, sig, ai.Rules(5, 0.55, 0.5))
    assert [(x.code, x.qty) for x in p] == [("A", 200)]  # 1/5 만 투자, 나머지 현금


# ---------------------------------------------------------------- 엔진 통과 (FR-AI-3/4)
def test_rebalance_places_market_orders_through_engine_with_prediction_ids(paper):
    ids = predict(paper.sf, SIGNAL_DAY, {"005930": 0.62, "000660": 0.58, "035420": 0.52})
    out = run(paper)
    assert [(o["code"], o["side"], o["qty"], o["status"]) for o in out] == [
        ("005930", "BUY", 28, "FILLED"),  # 10,000,000 / 5 = 2,000,000 -> 70,000원 28주
        ("000660", "BUY", 10, "FILLED"),
    ]
    orders = ai_orders(paper)
    assert all(o.source == "AI" and o.order_type == "MARKET" for o in orders)
    assert {o.code: o.prediction_id for o in orders} == {"005930": ids["005930"], "000660": ids["000660"]}
    assert paper.position("005930", paper.ai_account_id).qty == 28
    a = paper.account(paper.ai_account_id)
    assert a.cash < 10_000_000 - 28 * 70_000 - 10 * 200_000 and a.reserved_cash == 0  # 수수료 포함, 예약 해제
    assert paper.account().cash == 10_000_000  # 사용자 계좌는 그대로


def test_next_day_sells_when_probability_drops(paper):
    predict(paper.sf, SIGNAL_DAY, {"005930": 0.62})
    run(paper)
    predict(paper.sf, date(2026, 10, 21), {"005930": 0.48})
    out = run(paper, now=NEXT_DAY)
    assert [(o["side"], o["qty"], o["status"]) for o in out] == [("SELL", 28, "FILLED")]
    assert paper.position("005930", paper.ai_account_id).qty == 0


def test_rerun_same_day_does_not_duplicate_orders(paper):
    predict(paper.sf, SIGNAL_DAY, {"005930": 0.62})
    run(paper)
    assert run(paper) == []
    assert len(ai_orders(paper)) == 1


def test_stale_or_missing_signals_place_nothing(paper):
    predict(paper.sf, date(2026, 10, 16), {"005930": 0.9})  # 직전 거래일이 아닌 오래된 예측
    assert run(paper) == [] and ai_orders(paper) == []


def test_same_validation_as_users_inactive_symbol_is_rejected(paper):
    """AI 도 일반 검증을 그대로 받는다: 편출 종목 매수는 UNKNOWN_SYMBOL 로 거부·기록."""
    with paper.sf() as s:
        s.get(Stock, "035420").active = False
        s.commit()
    predict(paper.sf, SIGNAL_DAY, {"035420": 0.9})
    out = run(paper)
    assert out[0]["status"] == "REJECTED"
    assert ai_orders(paper)[0].reject_reason == "UNKNOWN_SYMBOL"


def test_buy_is_capped_by_market_order_reserve():
    """시장가 매수 예약은 상한가(+30%) 기준이라, 예약이 가용 현금 안에 들어오도록 수량을 줄인다."""

    class _S:
        pass

    from unittest.mock import patch

    from app.services.paper.fees import FeeRates

    rates = FeeRates(Decimal("0.000176923"), Decimal("0.000176923"), Decimal("0.002"))
    with patch.object(ai, "rates_on", return_value=rates):
        q = ai.affordable_qty(1_000_000, 10_000, 1_000, _S(), date(2026, 10, 21))
    assert q == 76  # 상한가 13,000원 x 77주 x (1+수수료) > 1,000,000
    with patch.object(ai, "rates_on", return_value=rates):
        assert ai.affordable_qty(1_000_000, None, 10, _S(), date(2026, 10, 21)) == 0


def test_job_rebalances_all_open_ai_accounts(paper, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "model_version", VERSION)
    predict(paper.sf, SIGNAL_DAY, {"005930": 0.62})
    r = fakeredis.FakeRedis(decode_responses=True)  # 비어 있으면 DB 종가로 합성 호가 (일반 주문과 같은 규칙)
    from app.models import PriceDaily

    with paper.sf() as s:
        s.add(
            PriceDaily(
                code="005930", date=SIGNAL_DAY, open=70_000, high=70_000, low=70_000, close=70_000, volume=1
            )
        )
        s.commit()
    res = paper_jobs.ai_rebalance_job(paper.sf, r, OPEN)
    assert res.status() == "success" and res.ok == 1
    assert [o.status for o in ai_orders(paper)] == ["FILLED"]


def test_ai_orders_api_shows_basis_prediction_FR_AI_4(paper, sf, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.common import get_store
    from app.core.config import settings
    from app.core.db import get_db
    from app.core.security import create_access_token
    from app.main import app
    from app.services.market_store import QuoteStore

    monkeypatch.setattr(settings, "jwt_secret", "test-secret-" + "x" * 32)
    ids = predict(sf, SIGNAL_DAY, {"005930": 0.62})
    run(paper)

    def db():
        with sf() as s:
            yield s

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_store] = lambda: QuoteStore(fakeredis.FakeAsyncRedis(decode_responses=True))
    try:
        with TestClient(app) as c:
            h = {"Authorization": f"Bearer {create_access_token(paper.user_id)}"}
            items = c.get("/api/v1/paper/orders?account=ai", headers=h).json()["data"]
            mine = c.get("/api/v1/paper/orders", headers=h).json()["data"]
    finally:
        app.dependency_overrides.clear()
    assert mine == []  # 사용자 계좌 주문과 섞이지 않는다
    assert items[0]["source"] == "AI" and items[0]["prediction_id"] == ids["005930"]
    assert items[0]["basis"] == {"p_up": 0.62, "as_of_date": "2026-10-20"}


# ---------------------------------------------------------------- 순위 모드 (AI_MODE=rank, 기본값)
RANK = ai.Rules(top_n=2, buy_threshold=0.55, sell_threshold=0.50, mode="rank", sell_rank=3)


def test_rank_mode_buys_top_n_even_when_all_probabilities_are_low():
    """보정된 확률이 모두 기준선(43%) 근처여도 순위 상위 N 을 산다."""
    sig = {
        c: ai.Signal(c, p, i)
        for i, (c, p) in enumerate([("A", 0.431), ("B", 0.430), ("C", 0.428), ("D", 0.425)])
    }
    prices = {c: 1_000 for c in sig}
    p = ai.plan({}, prices, 1_000_000, sig, RANK)
    assert [(x.side, x.code, x.qty) for x in p] == [("BUY", "A", 500), ("BUY", "B", 500)]
    assert p[0].reason.startswith("순위 1/4")


def test_rank_mode_sells_only_when_out_of_sell_rank_band():
    sig = {
        c: ai.Signal(c, p, i) for i, (c, p) in enumerate([("A", 0.44), ("B", 0.43), ("C", 0.42), ("D", 0.41)])
    }
    prices = {c: 1_000 for c in sig}
    p = ai.plan({"C": 100, "D": 100}, prices, 1_000_000, sig, RANK)
    sides = [(x.side, x.code) for x in p]
    assert ("SELL", "D") in sides and ("SELL", "C") not in sides  # C 는 3위(매도 기준 이내)라 유지
    assert sides[0] == ("SELL", "D")  # 매도가 먼저


def test_settings_default_mode_is_rank():
    from app.core.config import Settings

    r = ai.Rules.from_settings()
    assert Settings.model_fields["ai_mode"].default == "rank" and r.sell_rank >= r.top_n
