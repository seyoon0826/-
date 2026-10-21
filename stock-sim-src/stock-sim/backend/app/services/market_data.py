"""시세 추상화 (SPEC 5.1, FR-MD).

- `MarketDataProvider`: 구현체(pykrx / replay / kis)가 따르는 인터페이스
- `MarketDataService`: Provider 가 낸 시세를 Redis 캐시에 쓰고 pub/sub 으로 배포한다.
  체결 엔진과 WS API 는 Provider 를 직접 부르지 않고 Redis 만 구독한다 (FR-MD-6).
- 실행: `python -m app.services.market_data` (SPEC 4.1: marketdata 는 반드시 단일 인스턴스)
"""

from __future__ import annotations

import asyncio
import logging
import signal
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import Settings, settings

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
DEPTH = 10  # 호가 단수


class MarketDataUnavailable(Exception):
    """시세를 가져올 수 없음 (공급원 장애, 데이터 없음 등)."""


@dataclass(frozen=True)
class Quote:
    code: str
    price: int
    prev_close: int | None
    change: int
    change_pct: float  # % 단위 (1.27 = +1.27%)
    volume: int
    ts: datetime
    source: str  # pykrx | kis | replay


@dataclass(frozen=True)
class BookLevel:
    price: int
    qty: int


@dataclass(frozen=True)
class OrderBook:
    code: str
    asks: tuple[BookLevel, ...]  # 매도 1~10호가: 가격 오름차순 (1호가가 가장 낮음)
    bids: tuple[BookLevel, ...]  # 매수 1~10호가: 가격 내림차순 (1호가가 가장 높음)
    ts: datetime
    source: str


QuoteCallback = Callable[[Quote], Awaitable[None]]
OrderBookCallback = Callable[[OrderBook], Awaitable[None]]


def make_quote(
    code: str, price: int, prev_close: int | None, volume: int, ts: datetime, source: str
) -> Quote:
    change = price - prev_close if prev_close else 0
    pct = round(change / prev_close * 100, 2) if prev_close else 0.0
    return Quote(code, price, prev_close, change, pct, volume, ts, source)


class MarketDataProvider(ABC):
    """FR-MD-1. 모든 메서드는 비동기."""

    name: str = "base"

    @property
    def degraded(self) -> bool:
        """실시간 경로가 끊겨 대체 경로로 동작 중인지."""
        return False

    @abstractmethod
    async def get_quote(self, code: str) -> Quote: ...

    @abstractmethod
    async def get_orderbook(self, code: str) -> OrderBook: ...

    @abstractmethod
    async def subscribe(
        self, codes: Sequence[str], on_quote: QuoteCallback, on_orderbook: OrderBookCallback
    ) -> None:
        """시세 스트림 시작. 백그라운드 작업을 띄우고 바로 반환한다."""

    @abstractmethod
    async def close(self) -> None: ...


def build_provider(cfg: Settings = settings) -> MarketDataProvider:
    """FR-MD-5: MARKET_PROVIDER 로 구현체 선택. 키 없이 pykrx/replay 로 전체가 동작해야 한다."""
    if cfg.market_provider == "pykrx":
        from app.services.providers.pykrx_eod import PykrxEodProvider

        return PykrxEodProvider(depth_qty=cfg.eod_orderbook_qty)
    if cfg.market_provider == "replay":
        from app.services.providers.replay import ReplayProvider

        return ReplayProvider.from_database(speed=cfg.replay_speed)
    if cfg.market_provider == "kis":
        from app.services.providers.kis import KisProvider

        return KisProvider.from_settings(cfg)
    raise ValueError(f"알 수 없는 MARKET_PROVIDER: {cfg.market_provider}")


class MarketDataService:
    """Provider -> Redis 캐시 + pub/sub (FR-MD-6). 실시간 틱은 DB 에 저장하지 않는다 (FR-MD-7)."""

    def __init__(self, provider: MarketDataProvider, store) -> None:
        self.provider = provider
        self.store = store

    async def _on_quote(self, q: Quote) -> None:
        await self.store.publish_quote(q)

    async def _on_orderbook(self, ob: OrderBook) -> None:
        await self.store.publish_orderbook(ob)

    async def start(self, codes: Sequence[str]) -> None:
        await self.provider.subscribe(codes, self._on_quote, self._on_orderbook)
        await self.store.set_status(self.provider.name, self.provider.degraded)

    async def refresh_status(self) -> None:
        await self.store.set_status(self.provider.name, self.provider.degraded)

    async def stop(self) -> None:
        await self.provider.close()


def active_codes() -> list[str]:
    """DB 의 활성 종목 코드 (유니버스)."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Stock

    with SessionLocal() as db:
        return list(db.scalars(select(Stock.code).where(Stock.active.is_(True)).order_by(Stock.code)))


async def run_forever(service: MarketDataService, codes: Sequence[str], status_every: float = 5.0) -> None:
    await service.start(codes)
    log.info("marketdata 시작: provider=%s, 종목 %d개", service.provider.name, len(codes))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover (Windows)
            pass
    try:
        while not stop.is_set():
            await service.refresh_status()
            try:
                await asyncio.wait_for(stop.wait(), timeout=status_every)
            except TimeoutError:
                pass
    finally:
        await service.stop()


def main() -> None:  # pragma: no cover (프로세스 진입점)
    from dotenv import load_dotenv

    load_dotenv("../.env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from app.services.market_store import QuoteStore

    codes = active_codes()
    if not codes:
        log.warning("활성 종목이 없습니다. 먼저 stocks 를 적재하세요 (Phase 3).")
    service = MarketDataService(build_provider(), QuoteStore.from_url(settings.redis_url))
    asyncio.run(run_forever(service, codes))


if __name__ == "__main__":  # pragma: no cover
    main()
