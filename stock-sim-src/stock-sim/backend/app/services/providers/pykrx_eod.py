"""종가 기반 Provider (FR-MD-2). 키 없이 동작한다.

최근 종가를 현재가로 쓰고, 호가는 종가 ±1틱부터 1틱씩 합성한다. 실시간이 아니므로 `subscribe` 는
시작 시 한 번(그리고 refresh_sec 이 있으면 주기적으로) 값을 내보낸다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

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

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EodBar:
    code: str
    day: date
    close: int
    prev_close: int | None
    volume: int


def database_price_source(code: str) -> EodBar:
    """prices_daily 의 최근 두 거래일. DB 에 없으면 pykrx 로 직접 조회한다."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import PriceDaily

    with SessionLocal() as db:
        rows = db.execute(
            select(PriceDaily.date, PriceDaily.close, PriceDaily.volume)
            .where(PriceDaily.code == code)
            .order_by(PriceDaily.date.desc())
            .limit(2)
        ).all()
    if rows:
        prev = rows[1].close if len(rows) > 1 else None
        return EodBar(code, rows[0].date, rows[0].close, prev, rows[0].volume)
    return pykrx_price_source(code)


def pykrx_price_source(code: str) -> EodBar:
    from datetime import timedelta

    from app.clients import pykrx_client as pk

    try:
        end = pk.latest_business_day()
        start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=14)).strftime("%Y%m%d")
        df = pk.daily_ohlcv(code, start, end)
    except Exception as e:  # pykrx/KRX 장애
        raise MarketDataUnavailable(f"pykrx 조회 실패: {e.__class__.__name__}") from e
    if df.empty:
        raise MarketDataUnavailable(f"{code} 일봉이 없습니다")
    last = df.iloc[-1]
    prev = int(df.iloc[-2]["close"]) if len(df) > 1 else None
    return EodBar(code, df.index[-1].date(), int(last["close"]), prev, int(last["volume"]))


class PykrxEodProvider(MarketDataProvider):
    name = "pykrx"

    def __init__(
        self,
        price_source: Callable[[str], EodBar] = database_price_source,
        depth_qty: int = 10_000,
        refresh_sec: float | None = None,
    ) -> None:
        self._source = price_source
        self._qty = depth_qty
        self._refresh = refresh_sec
        self._task: asyncio.Task | None = None

    async def _bar(self, code: str) -> EodBar:
        return await asyncio.to_thread(self._source, code)

    @staticmethod
    def _ts(bar: EodBar) -> datetime:
        return datetime.combine(bar.day, time(15, 30), tzinfo=KST)  # 장 마감 시각

    async def get_quote(self, code: str) -> Quote:
        bar = await self._bar(code)
        return make_quote(code, bar.close, bar.prev_close, bar.volume, self._ts(bar), "pykrx")

    async def get_orderbook(self, code: str) -> OrderBook:
        bar = await self._bar(code)
        return synth_orderbook(code, bar.close, self._qty, self._ts(bar), "pykrx")

    async def _emit(self, codes, on_quote, on_orderbook) -> None:
        for code in codes:
            try:
                await on_quote(await self.get_quote(code))
                await on_orderbook(await self.get_orderbook(code))
            except MarketDataUnavailable as e:
                log.warning("%s 시세 건너뜀: %s", code, e)  # 일부 종목 실패해도 계속 (FR-CL-6 정신)

    async def subscribe(
        self, codes: Sequence[str], on_quote: QuoteCallback, on_orderbook: OrderBookCallback
    ) -> None:
        await self._emit(codes, on_quote, on_orderbook)
        if self._refresh:

            async def loop() -> None:
                while True:
                    await asyncio.sleep(self._refresh)
                    await self._emit(codes, on_quote, on_orderbook)

            self._task = asyncio.create_task(loop())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
