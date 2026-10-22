"""주문/체결 판단용 시세 스냅샷 만들기: Redis(실시간) 우선, 없으면 DB 최근 종가 (FR-MD-6, SPEC 7.1)."""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PriceDaily
from app.services.market_data import KST, OrderBook, Quote
from app.services.paper.engine import MarketSnapshot
from app.services.providers._book import synth_orderbook

EOD_BOOK_QTY = 10_000


def last_close_before(session: Session, code: str, day: date) -> tuple[date, int] | None:
    row = session.execute(
        select(PriceDaily.date, PriceDaily.close)
        .where(PriceDaily.code == code, PriceDaily.date < day)
        .order_by(PriceDaily.date.desc())
        .limit(1)
    ).first()
    return (row.date, row.close) if row else None


def last_close(session: Session, code: str) -> int | None:
    """가장 최근 종가 (평가용)."""
    return session.scalar(
        select(PriceDaily.close).where(PriceDaily.code == code).order_by(PriceDaily.date.desc()).limit(1)
    )


def build_snapshot(
    session: Session, code: str, quote: Quote | None, book: OrderBook | None, now: datetime
) -> MarketSnapshot:
    """기준가(전일 종가)는 실시간 공급원(kis/replay)이면 시세의 전일 종가, 아니면 DB 의 오늘 이전 마지막 종가.
    Redis 에 아무것도 없으면 DB 종가로 합성한 호가를 쓴다 (PykrxEodProvider 와 같은 규칙)."""
    today = now.astimezone(KST).date()
    db_last = last_close_before(session, code, today)
    if quote is not None and quote.source in ("kis", "replay") and quote.prev_close:
        prev_close: int | None = quote.prev_close
    else:
        prev_close = db_last[1] if db_last else None
    if quote is None and book is None and db_last is not None:
        ts = datetime.combine(db_last[0], time(15, 30), tzinfo=KST)
        book = synth_orderbook(code, db_last[1], EOD_BOOK_QTY, ts, "pykrx")
        return MarketSnapshot(book, db_last[1], ts, "pykrx", prev_close)
    return MarketSnapshot(
        book=book,
        last_price=quote.price if quote else None,
        quote_ts=quote.ts if quote else (book.ts if book else None),
        source=(quote.source if quote else (book.source if book else None)),
        prev_close=prev_close,
    )
