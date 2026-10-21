"""market_calendar 갱신과 조회 (SPEC 7.1, 12). 거래일 목록은 pykrx(코스피 지수 존재일)로 만든다."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.clients import pykrx_client as pk
from app.core.db import SessionLocal
from app.models import MarketCalendar
from app.pipelines.common import SessionFactory, job_run
from app.services.market_hours import predicted_is_open


def refresh(
    start: date,
    end: date,
    *,
    source=pk,
    session_factory: SessionFactory = SessionLocal,
) -> int:
    """[start, end] 의 모든 날짜에 대해 거래일 여부를 upsert 한다 (멱등)."""
    with job_run("refresh_calendar", session_factory) as res:
        open_days = set(source.trading_days(start.strftime("%Y%m%d"), end.strftime("%Y%m%d")))
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        rows = [{"date": d, "is_open": d in open_days} for d in days]
        with session_factory() as s:
            for i in range(0, len(rows), 500):
                stmt = insert(MarketCalendar).values(rows[i : i + 500])
                s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["date"], set_={"is_open": stmt.excluded.is_open}
                    )
                )
            s.commit()
        res.ok = len(rows)
    return len(rows)


def is_trading_day(d: date, session_factory: SessionFactory = SessionLocal) -> bool:
    """캘린더 행이 있으면 그 값, 없으면(미래 날짜 등) KRX 휴장일 캘린더로 예측한다."""
    with session_factory() as s:
        row = s.get(MarketCalendar, d)
    return row.is_open if row else predicted_is_open(d)


def is_first_trading_day_of_month(d: date, session_factory: SessionFactory = SessionLocal) -> bool:
    if not is_trading_day(d, session_factory):
        return False
    day = d.replace(day=1)
    while day < d:
        if is_trading_day(day, session_factory):
            return False
        day += timedelta(days=1)
    return True


def known_dates(session_factory: SessionFactory = SessionLocal) -> int:
    with session_factory() as s:
        return len(list(s.scalars(select(MarketCalendar.date))))
