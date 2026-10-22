"""체결/주문 이벤트 발행 (SPEC 4.2 ch:paper:{account_id}). 반드시 DB 커밋 뒤에 호출한다."""

from __future__ import annotations

import json
from typing import Any

import redis
import redis.asyncio as aioredis

from app.services.paper.engine import Events


def channel(account_id: int) -> str:
    return f"ch:paper:{account_id}"


async def publish_async(r: aioredis.Redis, events: Events) -> None:
    for account_id, payload in events.items:
        await r.publish(channel(account_id), json.dumps(payload, default=str))


def publish_sync(r: redis.Redis, events: Events) -> None:
    for account_id, payload in events.items:
        r.publish(channel(account_id), json.dumps(payload, default=str))


def as_list(events: Events) -> list[dict[str, Any]]:
    return [p for _, p in events.items]
