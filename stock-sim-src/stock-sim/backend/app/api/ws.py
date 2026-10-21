"""WS /ws/quotes (실시간 시세, SPEC 11). WS /ws/paper 는 Phase 4 에서 추가한다."""

import asyncio
import contextlib
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.common import get_store
from app.services.market_data import KST
from app.services.market_hours import market_state
from app.services.market_store import CH_ORDERBOOK, CH_QUOTE, QuoteStore, orderbook_to_dict, quote_to_dict

log = logging.getLogger(__name__)
router = APIRouter()

MAX_CODES = 30  # 연결당 최대 구독 종목
PING_SEC = 25


async def _status(store: QuoteStore) -> dict:
    st = await store.get_status()
    return {
        "type": "status",
        "market": market_state(datetime.now(KST)),
        "provider": st["provider"],
        "degraded": st["degraded"],
    }


def _to_client(channel: str, payload: dict) -> dict:
    if channel == CH_QUOTE:
        return {
            "type": "quote",
            "code": payload["code"],
            "price": payload["price"],
            "change_pct": payload["change_pct"],
            "volume": payload["volume"],
            "ts": payload["ts"],
        }
    return {
        "type": "orderbook",
        "code": payload["code"],
        "asks": [{"price": p, "qty": q} for p, q in payload["asks"]],
        "bids": [{"price": p, "qty": q} for p, q in payload["bids"]],
        "ts": payload["ts"],
    }


@router.websocket("/ws/quotes")
async def ws_quotes(ws: WebSocket, store: QuoteStore = Depends(get_store)):
    await ws.accept()
    subscribed: set[str] = set()
    await ws.send_json(await _status(store))

    async def reader() -> None:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except ValueError:
                await ws.send_json({"type": "error", "message": "JSON 형식이 아닙니다."})
                continue
            op, codes = msg.get("op"), [str(c) for c in msg.get("codes", [])]
            if op == "subscribe":
                if len(subscribed | set(codes)) > MAX_CODES:
                    await ws.send_json({"type": "error", "message": f"연결당 최대 {MAX_CODES}종목입니다."})
                    continue
                subscribed.update(codes)
                for code in codes:  # 구독 즉시 마지막 값을 한 번 보내 준다
                    q, ob = await store.get_quote(code), await store.get_orderbook(code)
                    if q:
                        await ws.send_json(_to_client(CH_QUOTE, quote_to_dict(q)))
                    if ob:
                        await ws.send_json(_to_client(CH_ORDERBOOK, orderbook_to_dict(ob)))
            elif op == "unsubscribe":
                subscribed.difference_update(codes)
            else:
                await ws.send_json({"type": "error", "message": "알 수 없는 op 입니다."})

    async def forwarder() -> None:
        pubsub = store.r.pubsub()
        await pubsub.subscribe(CH_QUOTE, CH_ORDERBOOK)
        try:
            async for m in pubsub.listen():
                if m["type"] != "message":
                    continue
                payload = json.loads(m["data"])
                if payload.get("code") in subscribed:
                    await ws.send_json(_to_client(m["channel"], payload))
        finally:
            await pubsub.aclose()

    async def pinger() -> None:
        while True:
            await asyncio.sleep(PING_SEC)
            await ws.send_json({"type": "ping"})
            await ws.send_json(await _status(store))

    tasks = [asyncio.create_task(c()) for c in (reader, forwarder, pinger)]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await t
