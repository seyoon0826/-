"""체결 워커: Redis 호가 이벤트(ch:orderbook)를 받아 미체결 주문을 판정한다.

SPEC 4.1: marketdata 프로세스에서 실행한다.

체결 엔진은 Redis 만 본다 (FR-MD-6). 동시성은 DB 락에만 의존한다 (engine 모듈 docstring 참고).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.services.market_store import CH_ORDERBOOK, QuoteStore, orderbook_from_dict
from app.services.paper.engine import Events, process_market_event
from app.services.paper.events import publish_async
from app.services.paper.market import build_snapshot

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


async def handle_orderbook(
    store: QuoteStore,
    session_factory: Callable[[], Session],
    payload: dict[str, object],
    now: Callable[[], datetime] = _now,
) -> int:
    """호가 이벤트 1건 처리. 바뀐 주문 수를 반환한다."""
    ob = orderbook_from_dict(payload)
    quote = await store.get_quote(ob.code)
    events = Events()

    def work() -> int:
        with session_factory() as s:
            t = now()
            mkt = build_snapshot(s, ob.code, quote, ob, t)
            changed = process_market_event(s, ob.code, mkt, t, events)
            s.commit()
            return len(changed)

    n = await asyncio.to_thread(work)
    await publish_async(store.r, events)
    return n


async def run_worker(store: QuoteStore, session_factory: Callable[[], Session]) -> None:  # pragma: no cover
    pubsub = store.r.pubsub()
    await pubsub.subscribe(CH_ORDERBOOK)
    log.info("체결 워커 시작")
    async for m in pubsub.listen():
        if m["type"] != "message":
            continue
        try:
            await handle_orderbook(store, session_factory, json.loads(m["data"]))
        except Exception:
            log.exception("호가 이벤트 처리 실패")  # 한 이벤트 실패가 워커를 멈추지 않게
