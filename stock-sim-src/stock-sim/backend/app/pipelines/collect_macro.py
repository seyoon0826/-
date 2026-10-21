"""ECOS 기준금리/원달러 환율 -> macro_daily (FR-CL-1). 연 단위로 나눠 조회한다."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.clients import ecos_client as ecos
from app.core.db import SessionLocal
from app.models import MacroDaily
from app.pipelines.common import JobResult, SessionFactory, job_run
from app.services.market_data import KST

log = logging.getLogger(__name__)

BACKFILL_START = date(2018, 1, 1)
CHUNK = 500


def parse_rows(rows: list[dict]) -> dict[date, Decimal]:
    """ECOS row(TIME='YYYYMMDD', DATA_VALUE) -> {날짜: 값}. 숫자가 아닌 값은 버린다."""
    out: dict[date, Decimal] = {}
    for r in rows:
        try:
            out[datetime.strptime(r["TIME"], "%Y%m%d").date()] = Decimal(
                str(r["DATA_VALUE"]).replace(",", "")
            )
        except (KeyError, ValueError, InvalidOperation):
            continue
    return out


def _windows(start: date, end: date):
    s = start
    while s <= end:
        e = min(date(s.year, 12, 31), end)
        yield s, e
        s = e + timedelta(days=1)


def run(
    *,
    fetchers: dict[str, Callable[[str, str], list[dict]]] | None = None,
    end: date | None = None,
    session_factory: SessionFactory = SessionLocal,
) -> JobResult:
    fetchers = fetchers or {"base_rate": ecos.base_rate, "usdkrw": ecos.usdkrw}
    with job_run("collect_macro", session_factory) as res:
        end = end or datetime.now(KST).date()
        for series, fetch in fetchers.items():
            try:
                with session_factory() as s:
                    last = s.scalar(select(func.max(MacroDaily.date)).where(MacroDaily.series == series))
                    start = last if last else BACKFILL_START
                    for ws, we in _windows(start, end):
                        values = parse_rows(fetch(ws.strftime("%Y%m%d"), we.strftime("%Y%m%d")))
                        rows = [{"series": series, "date": d, "value": v} for d, v in values.items()]
                        for i in range(0, len(rows), CHUNK):
                            stmt = insert(MacroDaily).values(rows[i : i + CHUNK])
                            s.execute(
                                stmt.on_conflict_do_update(
                                    index_elements=["series", "date"], set_={"value": stmt.excluded.value}
                                )
                            )
                    s.commit()
                res.ok += 1
            except Exception as e:
                log.warning("%s 수집 실패: %s", series, e.__class__.__name__)
                res.failed.append(f"{series}: {e.__class__.__name__}")
    return res
