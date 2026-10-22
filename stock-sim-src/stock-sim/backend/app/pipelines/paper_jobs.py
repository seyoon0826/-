"""모의투자 정기 작업 (SPEC 12): 09:00 대기 주문 처리, 15:30 미체결 만료, 16:30 포트폴리오 스냅샷."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import IndexDaily, Order, PaperAccount, PortfolioSnapshot, Position, PriceDaily
from app.pipelines.common import JobResult, SessionFactory, job_run
from app.services.market_data import KST
from app.services.market_store import orderbook_from_dict, orderbook_key, quote_from_dict, quote_key
from app.services.paper.engine import ACTIVE, Events, expire_day_orders, process_market_event
from app.services.paper.events import publish_sync
from app.services.paper.market import build_snapshot

log = logging.getLogger(__name__)


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def open_session_job(
    session_factory: SessionFactory = SessionLocal, r: redis.Redis | None = None, now: datetime | None = None
) -> JobResult:
    """09:00: 장 외에 접수된 주문을 현재 호가로 처리한다. 이후 체결은 워커가 호가 이벤트마다 처리한다."""
    r = r or _redis()
    now = now or datetime.now(UTC)
    with job_run("paper_open", session_factory) as res:
        with session_factory() as s:
            codes = sorted(set(s.scalars(select(Order.code).where(Order.status.in_(ACTIVE)))))
        for code in codes:
            q_raw, b_raw = r.hgetall(quote_key(code)), r.get(orderbook_key(code))
            quote = quote_from_dict(q_raw) if q_raw else None
            book = orderbook_from_dict(json.loads(b_raw)) if b_raw else None
            events = Events()
            with session_factory() as s:
                mkt = build_snapshot(s, code, quote, book, now)
                process_market_event(s, code, mkt, now, events)
                s.commit()
            publish_sync(r, events)
            res.ok += 1
    return res


def expire_job(
    session_factory: SessionFactory = SessionLocal, r: redis.Redis | None = None, now: datetime | None = None
) -> JobResult:
    """15:30: 미체결 DAY 주문 EXPIRED, 예약 해제."""
    r = r or _redis()
    now = now or datetime.now(UTC)
    with job_run("paper_expire", session_factory) as res:
        events = Events()
        with session_factory() as s:
            res.ok = expire_day_orders(s, now, events)
            s.commit()
        publish_sync(r, events)
    return res


def _close_on(s, code: str, day: date) -> int | None:  # type: ignore[no-untyped-def]
    return s.scalar(
        select(PriceDaily.close)
        .where(PriceDaily.code == code, PriceDaily.date <= day)
        .order_by(PriceDaily.date.desc())
        .limit(1)
    )


def _kospi_on(s, day: date) -> Decimal | None:  # type: ignore[no-untyped-def]
    return s.scalar(
        select(IndexDaily.close)
        .where(IndexDaily.index_code == "KOSPI", IndexDaily.date <= day)
        .order_by(IndexDaily.date.desc())
        .limit(1)
    )


def snapshot_job(session_factory: SessionFactory = SessionLocal, day: date | None = None) -> JobResult:
    """16:30: 열린 전 계좌의 일별 총자산과 KOSPI 대비 수익률 (계좌 시작일 기준)을 저장한다 (멱등)."""
    day = day or datetime.now(KST).date()
    with job_run("portfolio_snapshot", session_factory) as res:
        with session_factory() as s:
            accounts = list(s.scalars(select(PaperAccount).where(PaperAccount.closed_at.is_(None))))
            for a in accounts:
                stock_value = 0
                for p in s.scalars(select(Position).where(Position.account_id == a.id, Position.qty > 0)):
                    stock_value += p.qty * (_close_on(s, p.code, day) or int(p.avg_price))
                total = a.cash + stock_value
                start = a.created_at.astimezone(KST).date()
                k0, k1 = _kospi_on(s, start), _kospi_on(s, day)
                kospi_ret = (k1 / k0 - 1) * 100 if k0 and k1 else None
                row = {
                    "account_id": a.id,
                    "date": day,
                    "cash": a.cash,
                    "stock_value": stock_value,
                    "total_value": total,
                    "return_pct": Decimal(total - a.initial_cash) / a.initial_cash * 100,
                    "kospi_return_pct": kospi_ret,
                }
                stmt = insert(PortfolioSnapshot).values(row)
                s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["account_id", "date"],
                        set_={k: stmt.excluded[k] for k in row if k not in ("account_id", "date")},
                    )
                )
                res.ok += 1
            s.commit()
    return res
