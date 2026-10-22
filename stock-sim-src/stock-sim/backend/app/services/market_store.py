"""시세 Redis 캐시와 pub/sub (SPEC 4.2).

| 키/채널            | 내용                                   |
| quote:{code} (hash) | 최신 체결가, 등락률, 누적거래량, 시각   |
| orderbook:{code}    | 매수/매도 1~10호가 JSON                 |
| ch:quote            | 시세 이벤트 (JSON)                      |
| ch:orderbook        | 호가 이벤트 (JSON)                      |
| market:status       | 공급원 이름과 degraded 여부 (hash)      |
"""

from __future__ import annotations

import json
from datetime import datetime

import redis.asyncio as aioredis

from app.services.market_data import BookLevel, OrderBook, Quote

CH_QUOTE = "ch:quote"
CH_ORDERBOOK = "ch:orderbook"
STATUS_KEY = "market:status"


def quote_key(code: str) -> str:
    return f"quote:{code}"


def orderbook_key(code: str) -> str:
    return f"orderbook:{code}"


def quote_to_dict(q: Quote) -> dict:
    return {
        "code": q.code,
        "price": q.price,
        "prev_close": q.prev_close,
        "change": q.change,
        "change_pct": q.change_pct,
        "volume": q.volume,
        "ts": q.ts.isoformat(),
        "source": q.source,
    }


def quote_from_dict(d: dict) -> Quote:
    prev = d.get("prev_close")
    return Quote(
        code=d["code"],
        price=int(d["price"]),
        prev_close=int(prev) if prev not in (None, "", "None") else None,
        change=int(d["change"]),
        change_pct=float(d["change_pct"]),
        volume=int(d["volume"]),
        ts=datetime.fromisoformat(d["ts"]),
        source=d["source"],
    )


def orderbook_to_dict(ob: OrderBook) -> dict:
    return {
        "code": ob.code,
        "asks": [[lv.price, lv.qty] for lv in ob.asks],
        "bids": [[lv.price, lv.qty] for lv in ob.bids],
        "ts": ob.ts.isoformat(),
        "source": ob.source,
        "seq": ob.seq,
    }


def orderbook_from_dict(d: dict) -> OrderBook:
    return OrderBook(
        code=d["code"],
        asks=tuple(BookLevel(int(p), int(q)) for p, q in d["asks"]),
        bids=tuple(BookLevel(int(p), int(q)) for p, q in d["bids"]),
        ts=datetime.fromisoformat(d["ts"]),
        source=d["source"],
        seq=int(d.get("seq", 0)),
    )


def is_stale(ts: datetime, now: datetime, max_age_sec: float) -> bool:
    """시세가 너무 오래됐는지 (SPEC 14: 30초 넘으면 시장가 거부)."""
    return (now - ts).total_seconds() > max_age_sec


class QuoteStore:
    def __init__(self, client: aioredis.Redis) -> None:
        self.r = client

    @classmethod
    def from_url(cls, url: str) -> QuoteStore:
        return cls(aioredis.from_url(url, decode_responses=True))

    async def publish_quote(self, q: Quote) -> None:
        d = quote_to_dict(q)
        await self.r.hset(quote_key(q.code), mapping={k: "" if v is None else str(v) for k, v in d.items()})
        await self.r.publish(CH_QUOTE, json.dumps(d))

    async def publish_orderbook(self, ob: OrderBook) -> None:
        """종목별 이벤트 번호(seq)를 붙여 저장/발행한다."""
        seq = await self.r.incr(f"seq:orderbook:{ob.code}")
        d = orderbook_to_dict(ob) | {"seq": seq}
        payload = json.dumps(d)
        await self.r.set(orderbook_key(ob.code), payload)
        await self.r.publish(CH_ORDERBOOK, payload)

    async def get_quote(self, code: str) -> Quote | None:
        d = await self.r.hgetall(quote_key(code))
        return quote_from_dict(d) if d else None

    async def get_orderbook(self, code: str) -> OrderBook | None:
        raw = await self.r.get(orderbook_key(code))
        return orderbook_from_dict(json.loads(raw)) if raw else None

    async def set_status(self, provider: str, degraded: bool) -> None:
        await self.r.hset(STATUS_KEY, mapping={"provider": provider, "degraded": "1" if degraded else "0"})

    async def get_status(self) -> dict:
        d = await self.r.hgetall(STATUS_KEY)
        return {"provider": d.get("provider", "unknown"), "degraded": d.get("degraded") == "1"}

    async def close(self) -> None:
        await self.r.aclose()
