"""pykrx -> stocks / prices_daily / index_daily 적재 (증분 수집, FR-CL-1/2/6, FR-ST-1)."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.clients import pykrx_client as pk
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import IndexDaily, PriceDaily, Stock
from app.pipelines.common import JobResult, SessionFactory, job_run
from app.services.market_data import KST

log = logging.getLogger(__name__)

BACKFILL_START = date(2018, 1, 1)  # FR-CL-2
FINAL_AFTER = time(15, 40)  # 이 시각 전의 '오늘' 봉은 장중 값이라 확정 종가로 쓰지 않는다
CHUNK = 500


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _int(v) -> int | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return int(v)


def _dec(v) -> Decimal | None:
    """KRX 밸류에이션 값. 결측/무한대는 None. 0 은 그대로 둔다(적자 등 — 피처 단계에서 결측 처리)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return Decimal(str(round(f, 2)))


def upsert_prices(session, code: str, df) -> int:
    """멱등: (code, date) 충돌 시 값을 갱신한다. 종가가 없는 행은 건너뛰고, OHLC 는 정합성을 맞춘다."""
    rows = []
    for d, r in df.iterrows():
        close = _int(r["close"])
        if not close:
            continue
        o, h, lo, v = _int(r["open"]), _int(r["high"]), _int(r["low"]), _int(r["volume"]) or 0
        if v == 0 and not (o or h or lo):
            o = h = lo = close  # 거래정지일: KRX 는 시/고/저가 0, 종가만 직전가로 준다 -> 평평한 봉
        else:
            # 수정주가 반올림 오차로 종가가 고가를 1~2원 넘는 경우가 있다 -> 고/저가를 시·종가 범위로 보정
            h, lo = max(h or close, o or close, close), min(lo or close, o or close, close)
        foreign, inst = _int(r.get("foreign_net")), _int(r.get("inst_net"))
        if v == 0:
            # 거래가 없으면 순매수는 실제로 0원이다(KRX 는 빈 값을 준다). 거래정지는 volume=0 으로 구분된다.
            # 거래가 있었는데 값이 빈 날은 진짜 결측이므로 NULL 로 둔다 (피처 단계에서 커버리지로 처리).
            foreign, inst = foreign or 0, inst or 0
        row = {
            "code": code,
            "date": d,
            "open": o or close,
            "high": h,
            "low": lo,
            "close": close,
            "volume": v,
            "value": _int(r.get("value")),
            "foreign_net": foreign,
            "inst_net": inst,
            "market_cap": _int(r.get("market_cap")),
        }
        # 밸류에이션은 있을 때만 갱신 (없는 열로 기존 값을 지우지 않는다)
        for col in ("per", "pbr", "div_yield"):
            if col in df.columns:
                row[col] = _dec(r.get(col))
        rows.append(row)
    for i in range(0, len(rows), CHUNK):
        stmt = insert(PriceDaily).values(rows[i : i + CHUNK])
        cols = {c: stmt.excluded[c] for c in rows[0] if c not in ("code", "date")}
        session.execute(stmt.on_conflict_do_update(index_elements=["code", "date"], set_=cols))
    return len(rows)


def upsert_index(session, index_code: str, series) -> int:
    rows = [
        {"index_code": index_code, "date": d, "close": Decimal(str(v))} for d, v in series.items() if v == v
    ]
    for i in range(0, len(rows), CHUNK):
        stmt = insert(IndexDaily).values(rows[i : i + CHUNK])
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["index_code", "date"], set_={"close": stmt.excluded.close}
            )
        )
    return len(rows)


def final_end_date(source, now: datetime | None = None) -> date:
    """수집 종료일: 가장 최근 영업일. 단 그날이 오늘이고 아직 15:40 전이면 전 영업일(미확정 봉 제외)."""
    now = now or datetime.now(KST)
    end = datetime.strptime(source.latest_business_day(), "%Y%m%d").date()
    if end == now.date() and now.time() < FINAL_AFTER:
        end = datetime.strptime(source.latest_business_day(before=end - timedelta(days=1)), "%Y%m%d").date()
    return end


def active_codes(session_factory: SessionFactory = SessionLocal) -> list[str]:
    with session_factory() as s:
        return list(s.scalars(select(Stock.code).where(Stock.active.is_(True)).order_by(Stock.code)))


def select_universe(
    as_of: date,
    *,
    size: int | None = None,
    include_preferred: bool | None = None,
    source=pk,
    session_factory: SessionFactory = SessionLocal,
) -> list[str]:
    """FR-ST-1: 기준일 코스피 시총 상위 N종목을 active 로 두고 나머지는 active=false (데이터는 유지)."""
    size = size or settings.universe_size
    include_preferred = (
        settings.universe_include_preferred if include_preferred is None else include_preferred
    )
    with job_run("select_universe", session_factory) as res:
        top = source.top_marketcap(size, _ymd(as_of), include_preferred)
        codes = list(top["code"])
        if len(codes) < size:
            # KRX 가 빈/불완전 응답을 주면 기존 종목이 전부 편출되는 사고를 막는다
            raise RuntimeError(
                f"시총 상위 종목이 {len(codes)}개뿐입니다(기대 {size}개). 유니버스를 바꾸지 않습니다."
            )
        with session_factory() as s:
            for code, name in zip(top["code"], top["name"], strict=True):
                stmt = insert(Stock).values(code=code, name=name, market="KOSPI", active=True)
                s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["code"],  # aliases/sector 는 덮어쓰지 않는다
                        set_={"name": stmt.excluded.name, "active": True, "updated_at": func.now()},
                    )
                )
            s.execute(
                update(Stock).where(Stock.code.not_in(codes), Stock.active.is_(True)).values(active=False)
            )
            s.commit()
        res.ok = len(codes)
    return codes


def run(
    *,
    end: date | None = None,
    codes: list[str] | None = None,
    source=pk,
    session_factory: SessionFactory = SessionLocal,
    backfill_start: date = BACKFILL_START,
    now: datetime | None = None,
) -> JobResult:
    """활성 종목 일봉/수급/시총 + KOSPI 지수를 증분 수집한다. 일부 종목이 실패해도 계속한다 (FR-CL-6)."""
    with job_run("collect_prices", session_factory) as res:
        end = end or final_end_date(source, now)
        if codes is None:
            codes = active_codes(session_factory)
        if not codes:
            codes = select_universe(end, source=source, session_factory=session_factory)

        for code in codes:
            try:
                with session_factory() as s:
                    last = s.scalar(select(func.max(PriceDaily.date)).where(PriceDaily.code == code))
                    start = last if last else backfill_start  # 마지막 날부터 다시 받아 덮어쓴다(멱등)
                    if start > end:
                        res.ok += 1
                        continue
                    df = source.collect_full(code, _ymd(start), _ymd(end))
                    n = upsert_prices(s, code, df) if len(df) else 0
                    s.commit()
                log.info("%s: %d행 (%s~%s)", code, n, start, end)
                res.ok += 1
            except Exception as e:
                log.warning("%s 수집 실패: %s", code, e.__class__.__name__)
                res.failed.append(f"{code}: {e.__class__.__name__}")

        try:
            with session_factory() as s:
                last = s.scalar(select(func.max(IndexDaily.date)).where(IndexDaily.index_code == "KOSPI"))
                start = last if last else backfill_start
                if start <= end:
                    upsert_index(s, "KOSPI", source.index_close(pk.KOSPI_INDEX, _ymd(start), _ymd(end)))
                    s.commit()
            res.ok += 1
        except Exception as e:
            res.failed.append(f"KOSPI: {e.__class__.__name__}")
    return res
