"""계좌 생성/초기화, 포트폴리오/성과, 정기 작업(09:00 대기 주문, 15:30 만료, 16:30 스냅샷)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import fakeredis
import pytest
from sqlalchemy import select

from app.models import IndexDaily, Order, PaperAccount, PortfolioSnapshot, PriceDaily
from app.pipelines import paper_jobs
from app.services.market_data import KST
from app.services.market_store import orderbook_key, orderbook_to_dict
from app.services.paper import accounts as acc
from app.services.paper import errors as E
from app.services.paper.engine import Events
from tests.paper_support import CLOSED, NEXT_OPEN, OPEN, book, buy, mkt, sell, setup


@pytest.fixture
def paper(sf):
    p = setup(sf)
    yield p
    assert p.violations() == {}


# ---------------------------------------------------------------- 계좌 (FR-PT-1/2, FR-AI-1)
def test_create_makes_user_and_ai_accounts_with_same_cash_and_start(paper):
    u, a = paper.account(), paper.account(paper.ai_account_id)
    assert (u.type, a.type) == ("USER", "AI")
    assert u.initial_cash == a.initial_cash == u.cash == a.cash == 10_000_000
    assert u.created_at == a.created_at


def test_create_rejects_invalid_cash_and_duplicates(sf):
    p = setup(sf)
    with sf() as s:
        with pytest.raises(E.PaperError) as e:
            acc.create_accounts(s, p.user_id, 30_000_000, OPEN)  # 이미 계좌가 있음
        assert e.value.status == 409
    with sf() as s:
        from app.models import User

        u = User(email="v@example.com", password_hash="x")
        s.add(u)
        s.flush()
        with pytest.raises(E.PaperError):
            acc.create_accounts(s, u.id, 12_345, OPEN)  # 1천만/3천만/5천만/1억만 허용


def test_reset_cancels_orders_closes_old_and_keeps_history(paper, sf):
    paper.place(buy(10, 70_000), mkt(book(asks=[(70_000, 10)])))
    pending = paper.place(buy(5, 69_000), mkt(book(asks=[(70_000, 10)], seq=2)))
    with sf() as s:
        new_user, new_ai = acc.reset_accounts(s, paper.user_id, 50_000_000, OPEN, Events())
        s.commit()
        new_ids = (new_user.id, new_ai.id)
    old = paper.account()
    assert old.closed_at is not None and paper.order(pending).status == "CANCELED"
    assert paper.position().qty == 10  # 이전 계좌의 포지션/원장은 보존
    assert old.reserved_cash == 0
    for i in new_ids:
        a = paper.account(i)
        assert a.closed_at is None and a.cash == a.initial_cash == 50_000_000
    assert paper.account(paper.ai_account_id).closed_at is not None  # AI 도 같이 새로 시작


def test_portfolio_values_positions_at_given_prices(paper, sf):
    paper.place(buy(10, 70_000), mkt(book(asks=[(70_000, 10)])))
    with sf() as s:
        p = acc.portfolio(s, s.get(PaperAccount, paper.account_id), {"005930": 72_000})
    [h] = p["holdings"]
    assert (h["qty"], h["price"], h["value"], h["pnl"]) == (10, 72_000, 720_000, 20_000)
    assert h["return_pct"] == 2.86
    assert p["cash"] == 10_000_000 - 700_123 and p["stock_value"] == 720_000
    assert p["total_value"] == p["cash"] + 720_000  # 예약금은 cash 안에 포함
    assert p["return_pct"] == round((p["total_value"] - 10_000_000) / 10_000_000 * 100, 2)


# ---------------------------------------------------------------- 성과 (FR-PT-7)
def test_max_drawdown():
    assert acc.max_drawdown([100, 120, 90, 130, 117]) == -25.0  # 120 -> 90
    assert acc.max_drawdown([]) == 0.0


def test_performance_win_rate_and_turnover(paper, sf):
    paper.place(buy(10, 70_000), mkt(book(asks=[(70_000, 10)])))
    paper.place(sell(5, 72_000), mkt(book(bids=[(72_000, 5)], seq=2)))  # 이익
    paper.place(sell(5, 68_000), mkt(book(bids=[(68_000, 5)], seq=3)))  # 손실
    with sf() as s:
        perf = acc.performance(s, s.get(PaperAccount, paper.account_id))
    assert perf["sell_count"] == 2 and perf["win_rate"] == 50.0 and perf["trade_count"] == 3
    assert perf["turnover"] == round((700_000 + 360_000 + 340_000) / 2 / 10_000_000, 4)


def test_realized_sells_matches_engine_position_pnl(paper, sf):
    paper.place(buy(10, 70_000), mkt(book(asks=[(70_000, 10)])))
    paper.place(buy(10, 72_000), mkt(book(asks=[(72_000, 10)], seq=2)))
    paper.place(sell(20, 71_500), mkt(book(bids=[(71_500, 20)], seq=3)))
    with sf() as s:
        from app.models import Trade

        sells = acc.realized_sells(list(s.scalars(select(Trade))))
    assert sum(x.pnl for x in sells) == paper.position().realized_pnl


# ---------------------------------------------------------------- 정기 작업
@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


def test_open_job_processes_after_hours_orders_with_redis_book(paper, r):
    oid = paper.place(buy(5), mkt(None), now=CLOSED)
    assert paper.order(oid).queued
    ob = book(asks=[(70_200, 100)], seq=11, ts=NEXT_OPEN)
    r.set(orderbook_key("005930"), json.dumps(orderbook_to_dict(ob)))
    ps = r.pubsub()
    ps.subscribe(f"ch:paper:{paper.account_id}")
    ps.get_message(timeout=1)
    res = paper_jobs.open_session_job(paper.sf, r, NEXT_OPEN)
    assert res.status() == "success"
    o = paper.order(oid)
    assert o.status == "FILLED" and paper.trades(oid)[0].price == 70_200
    kinds = []
    while m := ps.get_message(ignore_subscribe_messages=True, timeout=0.2):
        kinds.append(json.loads(m["data"])["type"])
    assert "trade" in kinds and "order_update" in kinds  # 커밋 후 체결 알림 발행


def test_expire_job(paper, r):
    oid = paper.place(buy(5, 69_000), mkt(book(asks=[(70_000, 10)])))
    res = paper_jobs.expire_job(paper.sf, r, CLOSED)
    assert res.ok == 1 and paper.order(oid).status == "EXPIRED"


def test_snapshot_job_values_at_close_and_tracks_kospi_idempotently(paper, sf):
    paper.place(buy(10, 70_000), mkt(book(asks=[(70_000, 10)])))
    start = paper.account().created_at.astimezone(KST).date()
    today = date(2026, 10, 21)
    with sf() as s:
        s.add(PriceDaily(code="005930", date=today, open=1, high=73_000, low=1, close=72_000, volume=1))
        s.add_all(
            [
                IndexDaily(index_code="KOSPI", date=start, close=Decimal("4000")),
                IndexDaily(index_code="KOSPI", date=today, close=Decimal("4100")),
            ]
        )
        s.commit()
    paper_jobs.snapshot_job(sf, today)
    paper_jobs.snapshot_job(sf, today)  # 재실행해도 1행 (멱등)
    with sf() as s:
        snaps = list(
            s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.account_id == paper.account_id))
        )
    [snap] = snaps
    assert snap.stock_value == 720_000 and snap.total_value == snap.cash + 720_000
    assert snap.kospi_return_pct == Decimal("2.500000")
    assert snap.return_pct == round(Decimal(snap.total_value - 10_000_000) / 100_000, 6)


def test_reserve_does_not_change_total_value(paper, sf):
    """예약금은 cash 안에 있으므로 미체결 매수가 있어도 총자산은 그대로 (SPEC 7.7)."""
    paper.place(buy(10, 69_000), mkt(book(asks=[(70_000, 10)])))
    with sf() as s:
        p = acc.portfolio(s, s.get(PaperAccount, paper.account_id), {})
    assert p["total_value"] == 10_000_000 and p["reserved_cash"] > 0
    assert p["available_cash"] == 10_000_000 - p["reserved_cash"]


# ---------------------------------------------------------------- 체결 워커 (Redis 호가 이벤트 -> 체결)
async def test_worker_fills_on_redis_orderbook_event_and_publishes(paper, sf, monkeypatch):
    from app.services.market_store import QuoteStore, orderbook_to_dict
    from app.services.paper import worker

    paper.place(buy(10, 70_000), mkt(book(asks=[(70_100, 1)])))  # 대기 주문
    store = QuoteStore(fakeredis.FakeAsyncRedis(decode_responses=True))
    await store.publish_quote(make_quote_kis(70_000))
    ps = store.r.pubsub()
    await ps.subscribe(f"ch:paper:{paper.account_id}")
    await ps.get_message(timeout=1)
    payload = orderbook_to_dict(book(asks=[(70_000, 4)], seq=21))
    assert await worker.handle_orderbook(store, sf, payload, now=lambda: OPEN) == 1
    assert (
        await worker.handle_orderbook(store, sf, payload, now=lambda: OPEN) == 0
    )  # 같은 이벤트 재수신: 변화 없음
    with sf() as s:
        o = s.scalars(select(Order)).one()
    assert o.status == "PARTIAL" and o.filled_qty == 4
    kinds = []
    while m := await ps.get_message(ignore_subscribe_messages=True, timeout=0.2):
        kinds.append(json.loads(m["data"])["type"])
    assert kinds == ["trade", "order_update"]


async def test_worker_ignores_events_outside_market_hours(paper, sf):
    from app.services.market_store import QuoteStore, orderbook_to_dict
    from app.services.paper import worker

    paper.place(buy(10, 70_000), mkt(None), now=CLOSED)
    store = QuoteStore(fakeredis.FakeAsyncRedis(decode_responses=True))
    payload = orderbook_to_dict(book(asks=[(70_000, 10)], seq=22))
    assert await worker.handle_orderbook(store, sf, payload, now=lambda: CLOSED) == 0


def make_quote_kis(price):
    from app.services.market_data import make_quote

    return make_quote("005930", price, 69_000, 1, OPEN, "kis")


# ---------------------------------------------------------------- 불변식 검사기가 실제로 위반을 잡는지
def test_invariant_checker_detects_corruption(paper, sf):
    from app.models import PaperAccount, Position
    from app.services.paper.ledger import invariant_violations

    paper.place(buy(10, 70_000), mkt(book(asks=[(70_000, 10)])))
    with sf() as s:
        assert invariant_violations(s, paper.account_id) == []
        s.get(PaperAccount, paper.account_id).cash += 1  # 원장을 거치지 않은 잔고 변경
        s.get(Position, (paper.account_id, "005930")).reserved_qty = 3  # 매도 주문 없이 예약수량
        s.flush()
        errs = invariant_violations(s, paper.account_id)
        s.rollback()
    assert any("cash" in e for e in errs) and any("reserved_qty" in e for e in errs)
    with sf() as s:
        assert invariant_violations(s, 999_999) == ["계좌 999999 없음"]


def test_ledger_rejects_unknown_type(paper, sf):
    from app.models import PaperAccount
    from app.services.paper import ledger

    with sf() as s:
        with pytest.raises(ValueError):
            ledger.post(s, s.get(PaperAccount, paper.account_id), "BONUS", 1)
        assert ledger.post(s, s.get(PaperAccount, paper.account_id), "FEE", 0) is None  # 0원은 기록 안 함


# ---------------------------------------------------------------- 수익률 랭킹
def test_mask_name():
    assert acc.mask_name("김주식") == "김**"
    assert acc.mask_name("lee@gmail.com") == "l**"
    assert acc.mask_name("abcdefghij") == "a****"
    assert acc.mask_name(None) == "익명" and acc.mask_name("") == "익명"


def test_leaderboard_ranks_traded_user_accounts_with_ties_and_me(sf):
    from app.models import User

    p = setup(sf)  # 거래 없는 계좌는 빠진다
    with sf() as s:
        others = []
        for i, name in enumerate(["가나다", "라마바", "사아자"]):
            u = User(email=f"o{i}@example.com", password_hash="x", display_name=name)
            s.add(u)
            s.flush()
            a, _ = acc.create_accounts(s, u.id, 10_000_000, OPEN)
            others.append((u.id, a.id))
        s.commit()
    # 세 계좌 모두 같은 가격에 매수 -> 평가 가격만 바꿔 수익률을 다르게 만든다
    for _, aid in others:
        p.place(buy(10), mkt(book(asks=[(70_000, 100)])), account_id=aid)
    with sf() as s:
        lb = acc.leaderboard(s, {"005930": 77_000}, viewer_id=others[1][0])
    assert lb["total"] == 3 and len(lb["rows"]) == 3
    assert [r["rank"] for r in lb["rows"]] == [1, 1, 1]  # 같은 수익률은 같은 순위
    r0 = lb["rows"][0]
    assert r0["return_pct"] > 0 and r0["trades"] == 1 and "user_id" not in r0
    assert r0["ai_return_pct"] == 0.0 and r0["name"].endswith("**")
    assert lb["me"]["is_me"] and sum(r["is_me"] for r in lb["rows"]) == 1
    p.place(sell(10), mkt(book(bids=[(60_000, 100)])), account_id=others[2][1])  # 손절 -> 꼴찌
    with sf() as s:
        lb = acc.leaderboard(s, {"005930": 77_000})
    assert [r["rank"] for r in lb["rows"]] == [1, 1, 3] and lb["rows"][2]["return_pct"] < 0
    assert lb["me"] is None
