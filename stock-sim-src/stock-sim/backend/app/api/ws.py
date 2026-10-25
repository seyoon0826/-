"""WS /ws/quotes (실시간 시세), WS /ws/paper (체결 알림, 평가금액) — SPEC 11."""

import asyncio
import contextlib
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.common import ApiError, get_store
from app.api.deps import user_id_from_token
from app.core.db import get_db
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


PORTFOLIO_THROTTLE_SEC = 1.0  # 시세 변동 시 평가금액 푸시는 최대 1초 1회


AUTH_TIMEOUT_SEC = 10.0


@router.websocket("/ws/paper")
async def ws_paper(
    ws: WebSocket,
    store: QuoteStore = Depends(get_store),
    db: Session = Depends(get_db),
):
    """체결/주문 상태 이벤트(ch:paper:{account_id})와 평가금액을 푸시한다.
    토큰은 연결 직후 첫 메시지 {"type": "auth", "token": "..."} 로 받는다. 주소(?token=)로 받지 않는 이유:
    접속 로그(uvicorn access log, 프록시 로그)에 주소가 그대로 남아 토큰이 새기 때문이다."""
    from app.services.paper import accounts as acc
    from app.services.paper.events import channel

    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_json(), AUTH_TIMEOUT_SEC)
        token = first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else None
    except WebSocketDisconnect:
        return  # 인증 전에 클라이언트가 떠났다
    except (TimeoutError, ValueError):  # 10초 안에 인증하지 않았거나 JSON 이 아니다
        await ws.close(code=4401)
        return
    # 연결이 오래 살아 있으므로 DB 조회마다 트랜잭션을 바로 닫는다(rollback).
    # 열어 두면 idle-in-transaction 으로
    # 잠금을 쥐고 있어 다른 작업(DDL/TRUNCATE 등)을 막고, 체결 워커가 바꾼 최신 값도 못 본다.
    try:
        user_id = await run_in_threadpool(user_id_from_token, token, db)
        user, _ = await run_in_threadpool(acc.open_accounts, db, user_id)
        account_id = user.id if user else None  # rollback 이 객체를 만료시키기 전에 꺼내 둔다
    except ApiError:
        await ws.close(code=4401)
        return
    finally:
        await run_in_threadpool(db.rollback)
    if account_id is None:
        await ws.close(code=4404)
        return

    async def portfolio_msg() -> dict:
        """REST /paper/portfolio 와 같은 계산(실시간 시세 우선, 없으면 최근 종가)."""
        from app.api.paper import _summary

        try:
            a = await run_in_threadpool(lambda: acc.open_accounts(db, user_id)[0])
            if a is None:
                return {"type": "portfolio", "total_value": None, "return_pct": None}
            p = await _summary(db, store, a)
            return {"type": "portfolio", "total_value": p["total_value"], "return_pct": p["return_pct"]}
        finally:
            await run_in_threadpool(db.rollback)

    async def forwarder() -> None:
        pubsub = store.r.pubsub()
        await pubsub.subscribe(channel(account_id), CH_QUOTE)
        last_push = 0.0
        loop = asyncio.get_running_loop()
        try:
            async for m in pubsub.listen():
                if m["type"] != "message":
                    continue
                if m["channel"] == CH_QUOTE:
                    if loop.time() - last_push >= PORTFOLIO_THROTTLE_SEC:
                        last_push = loop.time()
                        await ws.send_json(await portfolio_msg())
                    continue
                await ws.send_json(json.loads(m["data"]))
        finally:
            await pubsub.aclose()

    async def reader() -> None:
        while True:
            await ws.receive_text()  # 클라이언트 메시지는 쓰지 않는다(연결 유지 확인용)

    async def pinger() -> None:
        while True:
            await asyncio.sleep(PING_SEC)
            await ws.send_json({"type": "ping"})

    await ws.send_json(await portfolio_msg())
    tasks = [asyncio.create_task(c()) for c in (reader, forwarder, pinger)]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await t
