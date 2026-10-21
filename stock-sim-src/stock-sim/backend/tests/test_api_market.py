"""시세 REST/WS API (SPEC 10.1, 11). Redis 는 fakeredis, DB 는 롤백되는 테스트 세션."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.api.common import get_store
from app.core.db import get_db
from app.main import app
from app.models import Disclosure, News, NewsStockMap, Prediction, PriceDaily, Stock
from app.services.market_data import KST, BookLevel, OrderBook, make_quote
from app.services.market_store import QuoteStore

LAST = date(2026, 10, 20)


@pytest.fixture
def redis_server():
    return fakeredis.FakeServer()


@pytest.fixture
def store(redis_server):
    return QuoteStore(fakeredis.FakeAsyncRedis(server=redis_server, decode_responses=True))


@pytest.fixture
def client(db, redis_server):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_store] = lambda: QuoteStore(
        fakeredis.FakeAsyncRedis(server=redis_server, decode_responses=True)
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db):
    db.add_all(
        [
            Stock(code="005930", name="삼성전자"),
            Stock(code="000660", name="SK하이닉스"),
            Stock(code="999999", name="편출", active=False),
        ]
    )
    db.flush()
    closes = [70_000, 71_200, 72_100]
    for i, c in enumerate(closes):
        d = LAST - timedelta(days=len(closes) - 1 - i)
        db.add(
            PriceDaily(
                code="005930", date=d, open=c - 100, high=c + 200, low=c - 300, close=c, volume=1_000 + i
            )
        )
    # 6개월 넘게 이전 봉 하나 (범위 필터 확인용)
    db.add(
        PriceDaily(
            code="005930", date=LAST - timedelta(days=400), open=1, high=1, low=1, close=60_000, volume=1
        )
    )
    db.flush()


def _publish(store, q=None, ob=None):
    async def go():
        if q:
            await store.publish_quote(q)
        if ob:
            await store.publish_orderbook(ob)

    asyncio.run(go())


def test_health_stays_at_root(client):
    assert client.get("/health").json() == {"ok": True}


def test_unknown_symbol_returns_error_envelope(client, seeded):
    r = client.get("/api/v1/stocks/123456/quote")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "UNKNOWN_SYMBOL", "message": "알 수 없는 종목입니다: 123456"}}


def test_quote_falls_back_to_last_close_when_redis_empty(client, seeded):
    r = client.get("/api/v1/stocks/005930/quote")
    assert r.status_code == 200
    d = r.json()["data"]
    assert (d["price"], d["change"], d["source"]) == (72_100, 900, "pykrx")
    assert d["ts"].endswith("+09:00")  # 시각은 KST (SPEC 10장)


def test_quote_prefers_redis_realtime(client, seeded, store):
    _publish(store, q=make_quote("005930", 72_500, 72_100, 9, datetime(2026, 10, 21, 10, tzinfo=KST), "kis"))
    d = client.get("/api/v1/stocks/005930/quote").json()["data"]
    assert (d["price"], d["source"], d["change"]) == (72_500, "kis", 400)


def test_quote_503_when_no_data_at_all(client, seeded):
    r = client.get("/api/v1/stocks/000660/quote")
    assert r.status_code == 503 and r.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_orderbook_synthesized_then_realtime(client, seeded, store):
    d = client.get("/api/v1/stocks/005930/orderbook").json()["data"]
    assert len(d["asks"]) == len(d["bids"]) == 10 and d["source"] == "pykrx"
    assert d["asks"][0]["price"] == 72_200 and d["bids"][0]["price"] == 72_000
    ob = OrderBook(
        "005930", (BookLevel(72_600, 3),), (BookLevel(72_500, 4),), datetime(2026, 10, 21, tzinfo=KST), "kis"
    )
    _publish(store, ob=ob)
    d = client.get("/api/v1/stocks/005930/orderbook").json()["data"]
    assert d["source"] == "kis" and d["asks"] == [{"price": 72_600, "qty": 3}]


def test_chart_range_filters_and_orders_candles(client, seeded):
    d = client.get("/api/v1/stocks/005930/chart?range=6M").json()["data"]
    assert [c["date"] for c in d["candles"]] == sorted(c["date"] for c in d["candles"])
    assert len(d["candles"]) == 3  # 400일 전 봉은 6M 범위 밖
    assert d["candles"][-1] == {
        "date": "2026-10-20",
        "open": 72_000,
        "high": 72_300,
        "low": 71_800,
        "close": 72_100,
        "volume": 1_002,
    }
    assert len(client.get("/api/v1/stocks/005930/chart?range=3Y").json()["data"]["candles"]) == 4
    assert client.get("/api/v1/stocks/005930/chart?range=2W").status_code == 422


def test_chart_markers_max_3_per_day_by_relevance_FR_ST_4(client, db, seeded):
    day = datetime(2026, 10, 19, 11, 0, tzinfo=KST)
    rels = [Decimal("0.3"), Decimal("1.0"), Decimal("0.6"), Decimal("0.6")]
    for i, rel in enumerate(rels):
        n = News(url=f"https://n.example/{i}", title=f"기사{i}", published_at=day + timedelta(minutes=i))
        db.add(n)
        db.flush()
        db.add(NewsStockMap(news_id=n.id, code="005930", relevance=rel))
    db.add(
        Disclosure(rcept_no="20261019000001", code="005930", corp_code="00126380", title="공시", filed_at=day)
    )
    db.flush()
    m = client.get("/api/v1/stocks/005930/chart?range=1M").json()["data"]["markers"]
    same_day = [x for x in m if x["date"] == "2026-10-19"]
    assert len(same_day) == 3  # 5건 중 최대 3건
    assert {x["type"] for x in same_day} <= {"news", "disclosure"}
    assert all(x["title"] != "기사0" for x in same_day)  # 관련도가 가장 낮은 기사 제외


def test_stock_list_has_price_and_latest_prediction_FR_ST_2(client, db, seeded):
    db.add(
        Prediction(
            code="005930",
            as_of_date=LAST,
            horizon=5,
            close_at_asof=72_100,
            p_up=Decimal("0.58"),
            p_outperform=Decimal("0.53"),
            q10=Decimal("-0.02"),
            q50=Decimal("0.01"),
            q90=Decimal("0.04"),
            model_version="v1",
            created_at=datetime.now(UTC),
        )
    )
    db.flush()
    rows = {r["code"]: r for r in client.get("/api/v1/stocks").json()["data"]}
    assert set(rows) == {"005930", "000660"}  # 편출 종목(active=false)은 제외
    assert (
        rows["005930"]["price"] == 72_100
        and rows["005930"]["p_up"] == 0.58
        and rows["005930"]["as_of_date"] == "2026-10-20"
    )
    assert rows["000660"]["price"] is None and rows["000660"]["p_up"] is None


# ---------- WebSocket ----------
def test_ws_status_then_snapshot_on_subscribe(client, seeded, store):
    _publish(store, q=make_quote("005930", 72_100, 71_200, 5, datetime(2026, 10, 20, 10, tzinfo=KST), "kis"))
    with client.websocket_connect("/api/v1/ws/quotes") as ws:
        st = ws.receive_json()
        assert st["type"] == "status" and st["market"] in ("open", "closed") and st["degraded"] is False
        ws.send_json({"op": "subscribe", "codes": ["005930"]})
        msg = ws.receive_json()
        assert msg["type"] == "quote" and msg["code"] == "005930" and msg["price"] == 72_100


def test_ws_forwards_only_subscribed_codes(client, seeded, store):
    with client.websocket_connect("/api/v1/ws/quotes") as ws:
        ws.receive_json()  # status
        ws.send_json({"op": "subscribe", "codes": ["005930"]})
        ts = datetime(2026, 10, 20, 10, tzinfo=KST)

        async def publish_both():
            await asyncio.sleep(0.3)  # 서버 쪽 pub/sub 구독이 붙을 시간
            await store.publish_quote(make_quote("000660", 200_000, 199_000, 1, ts, "kis"))  # 구독 안 한 종목
            await store.publish_quote(make_quote("005930", 72_300, 71_200, 2, ts, "kis"))

        import threading

        t = threading.Thread(target=lambda: asyncio.run(publish_both()))
        t.start()
        msg = ws.receive_json()
        t.join()
        assert msg["code"] == "005930" and msg["price"] == 72_300


def test_ws_rejects_more_than_30_codes_and_bad_input(client, seeded):
    with client.websocket_connect("/api/v1/ws/quotes") as ws:
        ws.receive_json()
        ws.send_json({"op": "subscribe", "codes": [f"{i:06d}" for i in range(31)]})
        assert ws.receive_json()["type"] == "error"
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"op": "nope"})
        assert ws.receive_json()["type"] == "error"
