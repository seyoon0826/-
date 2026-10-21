"""Redis 시세 캐시와 pub/sub (SPEC 4.2, FR-MD-6). fakeredis 사용 — 네트워크/Redis 서버 불필요."""

import asyncio
import json
from datetime import datetime, timedelta

import fakeredis

from app.services.market_data import KST, BookLevel, MarketDataService, OrderBook, make_quote
from app.services.market_store import CH_ORDERBOOK, CH_QUOTE, QuoteStore, is_stale
from app.services.providers.replay import DailyBar, ReplayProvider

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=KST)


def _store():
    return QuoteStore(fakeredis.FakeAsyncRedis(decode_responses=True))


async def test_quote_roundtrip_and_keys():
    store = _store()
    q = make_quote("005930", 72_100, 71_200, 123, NOW, "kis")
    await store.publish_quote(q)
    assert await store.get_quote("005930") == q
    raw = await store.r.hgetall("quote:005930")  # SPEC 4.2 키 형식
    assert raw["price"] == "72100" and raw["source"] == "kis"
    assert await store.get_quote("000660") is None


async def test_quote_without_prev_close_roundtrips():
    store = _store()
    q = make_quote("X", 1_000, None, 0, NOW, "pykrx")
    await store.publish_quote(q)
    assert (await store.get_quote("X")).prev_close is None


async def test_orderbook_roundtrip():
    store = _store()
    ob = OrderBook(
        "005930", (BookLevel(72_200, 10), BookLevel(72_300, 20)), (BookLevel(72_100, 5),), NOW, "kis"
    )
    await store.publish_orderbook(ob)
    assert await store.get_orderbook("005930") == ob
    assert json.loads(await store.r.get("orderbook:005930"))["asks"][0] == [72_200, 10]


async def test_pubsub_channels_receive_events():
    store = _store()
    ps = store.r.pubsub()
    await ps.subscribe(CH_QUOTE, CH_ORDERBOOK)
    await ps.get_message(timeout=1)  # subscribe 확인 메시지
    await ps.get_message(timeout=1)
    await store.publish_quote(make_quote("A", 100, 99, 1, NOW, "replay"))
    await store.publish_orderbook(OrderBook("A", (BookLevel(101, 1),), (BookLevel(99, 1),), NOW, "replay"))
    got = {}
    for _ in range(2):
        m = await ps.get_message(ignore_subscribe_messages=True, timeout=1)
        got[m["channel"]] = json.loads(m["data"])
    assert got[CH_QUOTE]["price"] == 100 and got[CH_ORDERBOOK]["asks"] == [[101, 1]]
    await ps.aclose()


async def test_status_defaults_and_set():
    store = _store()
    assert await store.get_status() == {"provider": "unknown", "degraded": False}
    await store.set_status("kis", True)
    assert await store.get_status() == {"provider": "kis", "degraded": True}


def test_is_stale_30_seconds():
    assert not is_stale(NOW, NOW + timedelta(seconds=30), 30)
    assert is_stale(NOW, NOW + timedelta(seconds=31), 30)


async def test_service_pipes_provider_into_redis_FR_MD_6():
    """Provider -> MarketDataService -> Redis: 소비자는 Redis 만 본다."""
    store = _store()
    bars = {"A": [DailyBar("A", NOW.date(), 100, 110, 95, 105, 1_000)]}
    service = MarketDataService(ReplayProvider.from_bars(bars, speed=0), store)
    ps = store.r.pubsub()
    await ps.subscribe(CH_QUOTE)
    await ps.get_message(timeout=1)  # subscribe 확인 메시지 소비
    await service.start(["A"])
    assert (await store.get_quote("A")).price == 105  # 마지막 틱(종가)
    assert (await store.get_orderbook("A")).asks[0].price > 105
    assert (await store.get_status())["provider"] == "replay"
    n = 0
    while await ps.get_message(ignore_subscribe_messages=True, timeout=0.2):
        n += 1
    assert n == 4
    await service.stop()
    await asyncio.sleep(0)
