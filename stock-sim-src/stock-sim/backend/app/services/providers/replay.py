"""재생 Provider (FR-MD-3). DB 일봉 또는 주어진 이벤트를 재생한다. 테스트/백테스트용.

결정적이다: 같은 입력이면 같은 순서로 같은 값을 내보낸다. speed <= 0 이면 대기 없이 즉시 재생한다.
일봉 1개는 시가 -> (저가/고가) -> 종가의 4틱으로 풀어 낸다. 시뮬레이션 시간 1단위 = 1초.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.services.market_data import (
    KST,
    MarketDataProvider,
    MarketDataUnavailable,
    OrderBook,
    OrderBookCallback,
    Quote,
    QuoteCallback,
    make_quote,
)
from app.services.providers._book import synth_orderbook
from app.services.providers.pykrx_eod import EodBar  # noqa: F401  (재export)

TICKS_PER_DAY = 4


@dataclass(frozen=True)
class DailyBar:
    code: str
    day: object  # datetime.date
    open: int
    high: int
    low: int
    close: int
    volume: int


@dataclass(frozen=True)
class ReplayEvent:
    t: float  # 시뮬레이션 시각(초). 같은 t 안에서는 입력 순서를 유지한다
    quote: Quote
    book: OrderBook


def bars_to_events(
    bars: dict[str, list[DailyBar]], depth_qty: int = 10_000, base_ts: datetime | None = None
) -> list[ReplayEvent]:
    events: list[tuple[float, int, ReplayEvent]] = []
    seq = 0
    for code in sorted(bars):
        prev_close: int | None = None
        for i, b in enumerate(bars[code]):
            path = [b.open, b.low, b.high, b.close] if b.close >= b.open else [b.open, b.high, b.low, b.close]
            for k, price in enumerate(path):
                t = float(i * TICKS_PER_DAY + k)
                ts = (base_ts or datetime.combine(b.day, time(9, 0), tzinfo=KST)) + timedelta(minutes=k * 100)
                vol = b.volume * (k + 1) // TICKS_PER_DAY  # 당일 누적거래량
                q = make_quote(code, price, prev_close, vol, ts, "replay")
                ob = synth_orderbook(code, price, depth_qty, ts, "replay")
                events.append((t, seq, ReplayEvent(t, q, ob)))
                seq += 1
            prev_close = b.close
    events.sort(key=lambda x: (x[0], x[1]))
    return [e for _, _, e in events]


class ReplayProvider(MarketDataProvider):
    name = "replay"

    def __init__(self, events: Sequence[ReplayEvent], speed: float = 60.0) -> None:
        self._events = list(events)
        self._speed = speed
        self._latest_q: dict[str, Quote] = {}
        self._latest_b: dict[str, OrderBook] = {}
        for e in self._events:  # 재생 전에도 조회 가능하도록 첫 값을 시드
            self._latest_q.setdefault(e.quote.code, e.quote)
            self._latest_b.setdefault(e.book.code, e.book)
        self._task: asyncio.Task | None = None

    @classmethod
    def from_bars(cls, bars: dict[str, list[DailyBar]], speed: float = 60.0, depth_qty: int = 10_000):
        return cls(bars_to_events(bars, depth_qty), speed)

    @classmethod
    def from_database(cls, speed: float = 60.0, days: int = 60, depth_qty: int = 10_000):
        from sqlalchemy import select

        from app.core.db import SessionLocal
        from app.models import PriceDaily, Stock

        bars: dict[str, list[DailyBar]] = {}
        with SessionLocal() as db:
            for code in db.scalars(select(Stock.code).where(Stock.active.is_(True))):
                rows = db.execute(
                    select(PriceDaily)
                    .where(PriceDaily.code == code)
                    .order_by(PriceDaily.date.desc())
                    .limit(days)
                ).scalars()
                bars[code] = [
                    DailyBar(code, r.date, r.open, r.high, r.low, r.close, r.volume)
                    for r in reversed(list(rows))
                ]
        return cls.from_bars({c: b for c, b in bars.items() if b}, speed, depth_qty)

    async def get_quote(self, code: str) -> Quote:
        try:
            return self._latest_q[code]
        except KeyError:
            raise MarketDataUnavailable(f"{code} 재생 데이터 없음") from None

    async def get_orderbook(self, code: str) -> OrderBook:
        try:
            return self._latest_b[code]
        except KeyError:
            raise MarketDataUnavailable(f"{code} 재생 데이터 없음") from None

    async def play(
        self, codes: Sequence[str], on_quote: QuoteCallback, on_orderbook: OrderBookCallback
    ) -> None:
        wanted = set(codes)
        prev_t = 0.0
        for e in self._events:
            if e.quote.code not in wanted:
                continue
            if self._speed > 0 and e.t > prev_t:
                await asyncio.sleep((e.t - prev_t) / self._speed)
            prev_t = e.t
            self._latest_q[e.quote.code] = e.quote
            self._latest_b[e.book.code] = e.book
            await on_quote(e.quote)
            await on_orderbook(e.book)

    async def subscribe(
        self, codes: Sequence[str], on_quote: QuoteCallback, on_orderbook: OrderBookCallback
    ) -> None:
        if self._speed <= 0:  # 결정적 테스트: 끝까지 재생하고 반환
            await self.play(codes, on_quote, on_orderbook)
        else:
            self._task = asyncio.create_task(self.play(codes, on_quote, on_orderbook))

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
