"""모의투자 API (SPEC 10.4, 11, FR-AU-3/4). 실제 DB(커밋), Redis 는 fakeredis, 시각은 장중으로 고정."""

from __future__ import annotations

import asyncio

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.api import paper as paper_api
from app.api.common import get_store
from app.core.config import settings
from app.core.db import get_db
from app.core.security import create_access_token
from app.main import app
from app.models import User
from app.services.market_data import make_quote
from app.services.market_store import QuoteStore
from tests.paper_support import OPEN, book, setup


@pytest.fixture
def env(sf, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-" + "x" * 32)
    monkeypatch.setattr(paper_api, "_now", lambda: OPEN)
    server = fakeredis.FakeServer()

    def store():
        return QuoteStore(fakeredis.FakeAsyncRedis(server=server, decode_responses=True))

    def db():
        with sf() as s:
            yield s

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_store] = store
    with TestClient(app) as c:
        yield c, sf, store
    app.dependency_overrides.clear()


def _market(store, asks=((70_000, 100),), bids=((69_900, 100),), price=70_000, seq_book=None):
    async def go():
        s = store()
        await s.publish_quote(make_quote("005930", price, 69_000, 1_000, OPEN, "kis"))
        await s.publish_orderbook(book(asks=asks, bids=bids, ts=OPEN))

    asyncio.run(go())


@pytest.fixture
def user(env):
    c, sf, store = env
    p = setup(sf)
    return p, {"Authorization": f"Bearer {create_access_token(p.user_id)}"}


def _other_user_headers(sf):
    with sf() as s:
        u = User(email="other@example.com", password_hash="x")
        s.add(u)
        s.commit()
        return {"Authorization": f"Bearer {create_access_token(u.id)}"}, u.id


# ---------------------------------------------------------------- 인증 (FR-AU-3)
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/paper/accounts/me"),
        ("post", "/api/v1/paper/orders"),
        ("get", "/api/v1/paper/portfolio"),
        ("get", "/api/v1/paper/orders"),
    ],
)
def test_login_required(env, method, path):
    c, *_ = env
    r = getattr(c, method)(path)
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    r = getattr(c, method)(path, headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_quotes_stay_public(env):
    c, *_ = env
    assert c.get("/health").status_code == 200  # 시세/예측은 비로그인 허용 (FR-AU-3)


# ---------------------------------------------------------------- 계좌
def test_account_lifecycle(env):
    c, sf, _ = env
    p = setup(sf)  # setup 이 이미 계좌를 만든 사용자
    h_new, _ = _other_user_headers(sf)
    assert c.get("/api/v1/paper/accounts/me", headers=h_new).json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
    assert c.post("/api/v1/paper/accounts", json={"initial_cash": 12_345}, headers=h_new).status_code == 422
    r = c.post("/api/v1/paper/accounts", json={"initial_cash": 30_000_000}, headers=h_new)
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["user"]["total_value"] == d["ai"]["total_value"] == 30_000_000
    assert (
        c.post("/api/v1/paper/accounts", json={"initial_cash": 30_000_000}, headers=h_new).status_code == 409
    )
    r = c.post("/api/v1/paper/accounts/reset", json={"initial_cash": 100_000_000}, headers=h_new)
    assert r.json()["data"]["user"]["initial_cash"] == 100_000_000
    assert p.user_id


# ---------------------------------------------------------------- 주문
def test_order_filled_returns_201_with_trades(env, user):
    c, sf, store = env
    p, h = user
    _market(store)
    r = c.post(
        "/api/v1/paper/orders",
        json={"code": "005930", "side": "BUY", "order_type": "MARKET", "qty": 3},
        headers=h,
    )
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["status"] == "FILLED" and [(t["price"], t["qty"]) for t in d["trades"]] == [(70_000, 3)]
    assert d["created_at"].endswith("+09:00")


@pytest.mark.parametrize(
    "body,code",
    [
        (
            {"code": "005930", "side": "BUY", "order_type": "LIMIT", "qty": 1, "limit_price": 70_050},
            "INVALID_TICK",
        ),
        (
            {"code": "005930", "side": "BUY", "order_type": "LIMIT", "qty": 1.5, "limit_price": 70_000},
            "INVALID_QTY",
        ),
        (
            {"code": "123456", "side": "BUY", "order_type": "LIMIT", "qty": 1, "limit_price": 70_000},
            "UNKNOWN_SYMBOL",
        ),
        ({"code": "005930", "side": "SELL", "order_type": "MARKET", "qty": 1}, "INSUFFICIENT_QTY"),
    ],
)
def test_rejected_order_is_422_with_code_and_saved(env, user, body, code):
    c, sf, store = env
    p, h = user
    _market(store)
    r = c.post("/api/v1/paper/orders", json=body, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == code
    listed = c.get("/api/v1/paper/orders?status=REJECTED", headers=h).json()["data"]
    assert [o["reject_reason"] for o in listed] == [code]  # 사용자에게 내역 표시용으로 저장됨


def test_idempotency_key_header(env, user):
    c, sf, store = env
    p, h = user
    _market(store)
    body = {"code": "005930", "side": "BUY", "order_type": "LIMIT", "qty": 2, "limit_price": 70_000}
    a = c.post("/api/v1/paper/orders", json=body, headers={**h, "Idempotency-Key": "abc"}).json()["data"]
    b = c.post("/api/v1/paper/orders", json=body, headers={**h, "Idempotency-Key": "abc"}).json()["data"]
    assert a["id"] == b["id"] and len(c.get("/api/v1/paper/trades", headers=h).json()["data"]) == 1


def test_cancel_own_pending_but_not_others_or_filled(env, user):
    c, sf, store = env
    p, h = user
    _market(store)
    pend = c.post(
        "/api/v1/paper/orders",
        json={"code": "005930", "side": "BUY", "order_type": "LIMIT", "qty": 1, "limit_price": 69_000},
        headers=h,
    ).json()["data"]
    filled = c.post(
        "/api/v1/paper/orders",
        json={"code": "005930", "side": "BUY", "order_type": "MARKET", "qty": 1},
        headers=h,
    ).json()["data"]
    h_other, _ = _other_user_headers(sf)
    c.post("/api/v1/paper/accounts", json={"initial_cash": 10_000_000}, headers=h_other)
    r = c.delete(f"/api/v1/paper/orders/{pend['id']}", headers=h_other)
    assert r.status_code == 404  # FR-AU-4: 타인 주문은 존재 자체를 드러내지 않는다
    assert c.delete(f"/api/v1/paper/orders/{filled['id']}", headers=h).status_code == 409
    r = c.delete(f"/api/v1/paper/orders/{pend['id']}", headers=h)
    assert r.status_code == 200 and r.json()["data"]["status"] == "CANCELED"
    assert c.get("/api/v1/paper/orders", headers=h_other).json()["data"] == []  # 남의 주문은 안 보인다


def test_order_list_filters_and_cursor(env, user):
    c, sf, store = env
    p, h = user
    _market(store)
    for price in (69_000, 69_100, 69_200):
        c.post(
            "/api/v1/paper/orders",
            json={"code": "005930", "side": "BUY", "order_type": "LIMIT", "qty": 1, "limit_price": price},
            headers=h,
        )
    page1 = c.get("/api/v1/paper/orders?limit=2", headers=h).json()
    assert len(page1["data"]) == 2 and page1["meta"]["next_cursor"]
    assert c.get("/api/v1/paper/orders?code=000660", headers=h).json()["data"] == []
    assert len(c.get("/api/v1/paper/orders?status=PENDING,PARTIAL", headers=h).json()["data"]) == 3


def test_portfolio_and_performance(env, user):
    c, sf, store = env
    p, h = user
    _market(store)
    c.post(
        "/api/v1/paper/orders",
        json={"code": "005930", "side": "BUY", "order_type": "MARKET", "qty": 10},
        headers=h,
    )
    _market(store, price=71_000)
    d = c.get("/api/v1/paper/portfolio", headers=h).json()["data"]
    [hold] = d["holdings"]
    assert hold["price"] == 71_000 and hold["value"] == 710_000  # Redis 실시간 시세로 평가
    perf = c.get("/api/v1/paper/performance?account=ai", headers=h).json()["data"]
    assert perf["type"] == "AI" and perf["trade_count"] == 0


def test_estimate_matches_engine_costs(env, user):
    c, sf, store = env
    p, h = user
    _market(store, asks=((70_000, 5), (70_100, 10)))
    d = c.get("/api/v1/paper/estimate?code=005930&side=BUY&order_type=MARKET&qty=10", headers=h).json()[
        "data"
    ]
    assert d["amount"] == 5 * 70_000 + 5 * 70_100 and d["fee"] == int((d["amount"] * 0.000176923) // 1)
    assert d["total"] == d["amount"] + d["fee"] and d["required_reserve"] > d["total"]
    q = "code=005930&side=SELL&order_type=LIMIT&qty=10&limit_price=70000"
    d = c.get(f"/api/v1/paper/estimate?{q}", headers=h).json()["data"]
    assert d["tax"] == 1_400 and d["total"] == d["amount"] - d["fee"] - d["tax"]


# ---------------------------------------------------------------- WS /ws/paper (SPEC 11)
def test_ws_paper_rejects_bad_token(env):
    c, *_ = env
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect("/api/v1/ws/paper") as ws:
        ws.send_json({"type": "auth", "token": "bad"})
        ws.receive_json()
    assert e.value.code == 4401


def test_ws_paper_ignores_token_in_url_so_it_never_reaches_access_logs(env, user):
    """토큰은 첫 메시지로만 받는다. 주소의 ?token= 은 무시 (접속 로그에 남지 않게)."""
    c, *_ = env
    _, h = user
    token = h["Authorization"].split()[1]
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect) as e,
        c.websocket_connect(f"/api/v1/ws/paper?token={token}") as ws,
    ):
        ws.send_json({"type": "hello"})  # auth 메시지가 아니면 거절
        ws.receive_json()
    assert e.value.code == 4401


def test_ws_paper_pushes_portfolio_then_order_and_trade_events(env, user):
    c, sf, store = env
    p, h = user
    _market(store)
    token = h["Authorization"].split()[1]
    with c.websocket_connect("/api/v1/ws/paper") as ws:
        ws.send_json({"type": "auth", "token": token})
        first = ws.receive_json()
        assert first["type"] == "portfolio" and first["total_value"] == 10_000_000
        import time

        time.sleep(0.3)  # 서버 쪽 pub/sub 구독이 붙을 시간
        c.post(
            "/api/v1/paper/orders",
            json={"code": "005930", "side": "BUY", "order_type": "MARKET", "qty": 1},
            headers=h,
        )
        got = [ws.receive_json()["type"] for _ in range(2)]
    assert got == ["trade", "order_update"]


def test_leaderboard_is_public_and_marks_me_when_logged_in(env, user):
    c, sf, store = env
    p, h = user
    _market(store)
    assert (
        c.post(
            "/api/v1/paper/orders",
            json={"code": "005930", "side": "BUY", "order_type": "MARKET", "qty": 3},
            headers=h,
        ).status_code
        == 201
    )
    anon = c.get("/api/v1/paper/leaderboard")
    assert anon.status_code == 200 and anon.json()["data"]["total"] == 1
    assert anon.json()["data"]["me"] is None and anon.json()["meta"]["valuation"] == "최근 종가"
    mine = c.get("/api/v1/paper/leaderboard", headers=h).json()["data"]
    assert mine["me"]["rank"] == 1 and mine["rows"][0]["is_me"]
    bad = c.get("/api/v1/paper/leaderboard", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 200 and bad.json()["data"]["me"] is None  # 틀린 토큰도 공개 화면은 보인다
    with sf() as s:  # 관리자가 랭킹에서 숨기면 빠진다
        s.get(User, p.user_id).hidden_from_ranking = True
        s.commit()
    assert c.get("/api/v1/paper/leaderboard").json()["data"]["total"] == 0
