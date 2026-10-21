"""DART 공시 -> disclosures (FR-CL-1/6). corp_code 매핑은 주 1회 갱신해 파일에 캐시한다.

filed_at: DART 목록은 접수'일자'만 주고 시각이 없다. 장 마감 후 공시를 당일 피처로 쓰는
미래 정보 누수를 막기 위해 해당일 23:59 KST(= 장 마감 후)로 저장한다.
즉 학습에서는 항상 다음 거래일 피처로 매핑된다 (SPEC 8.1, 보수적).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.clients import dart_client as dart
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Disclosure, Stock
from app.pipelines.common import JobResult, SessionFactory, job_run
from app.services.market_data import KST

log = logging.getLogger(__name__)

BACKFILL_START = date(2018, 1, 1)
CORP_CODE_MAX_AGE = timedelta(days=7)


def corp_code_cache_path() -> Path:
    return Path(settings.cache_dir) / "dart_corp_codes.json"


def load_corp_map(
    loader: Callable[[], dict[str, str]] = dart.load_corp_codes,
    *,
    path: Path | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, str]:
    """캐시가 7일 이내면 파일을, 아니면 DART 에서 새로 받아 저장한다."""
    path, now = path or corp_code_cache_path(), now or datetime.now(UTC)
    if not force and path.exists():
        cache = json.loads(path.read_text())
        if now - datetime.fromisoformat(cache["fetched_at"]) < CORP_CODE_MAX_AGE:
            return cache["map"]
    mapping = loader()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": now.isoformat(), "map": mapping}, ensure_ascii=False))
    return mapping


def refresh_corp_codes(loader=dart.load_corp_codes, session_factory: SessionFactory = SessionLocal) -> int:
    """주 1회 작업 (SPEC 12)."""
    with job_run("refresh_corp_codes", session_factory) as res:
        res.ok = len(load_corp_map(loader, force=True))
    return res.ok


def to_filed_at(rcept_dt: str) -> datetime:
    d = datetime.strptime(rcept_dt, "%Y%m%d").date()
    return datetime.combine(d, time(23, 59), tzinfo=KST)


def report_type(report_nm: str) -> str | None:
    name = (report_nm or "").strip()
    return name.split("(")[0].strip() or None if name else None


def run(
    *,
    list_fn=dart.disclosures,
    corp_loader=dart.load_corp_codes,
    end: date | None = None,
    session_factory: SessionFactory = SessionLocal,
    corp_map: dict[str, str] | None = None,
) -> JobResult:
    with job_run("collect_disclosures", session_factory) as res:
        end = end or datetime.now(KST).date()
        corp_map = corp_map if corp_map is not None else load_corp_map(corp_loader)
        with session_factory() as s:
            codes = list(s.scalars(select(Stock.code).where(Stock.active.is_(True)).order_by(Stock.code)))
        for code in codes:
            corp = corp_map.get(code)
            if not corp:
                res.failed.append(f"{code}: corp_code 없음")
                continue
            try:
                with session_factory() as s:
                    last = s.scalar(select(func.max(Disclosure.filed_at)).where(Disclosure.code == code))
                    start = (last.astimezone(KST).date() - timedelta(days=1)) if last else BACKFILL_START
                    items = list_fn(corp, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
                    rows = [
                        {
                            "rcept_no": it["rcept_no"],
                            "code": code,
                            "corp_code": it.get("corp_code") or corp,
                            "title": it.get("report_nm", ""),
                            "filed_at": to_filed_at(it["rcept_dt"]),
                            "report_type": report_type(it.get("report_nm", "")),
                        }
                        for it in items
                    ]
                    if rows:
                        s.execute(
                            insert(Disclosure)
                            .values(rows)
                            .on_conflict_do_nothing(index_elements=["rcept_no"])
                        )
                    s.commit()
                res.ok += 1
            except Exception as e:
                log.warning("%s 공시 수집 실패: %s", code, e.__class__.__name__)
                res.failed.append(f"{code}: {e.__class__.__name__}")
    return res
