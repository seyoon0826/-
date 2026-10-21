"""파이프라인 공통: job_runs 기록(FR-CL-5), 실패 격리, 최근 성공 조회."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import JobRun

log = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
MAX_ERROR_CHARS = 2000


@dataclass
class JobResult:
    ok: int = 0
    failed: list[str] = field(default_factory=list)  # "항목: 오류 종류" (키/URL 등은 넣지 않는다)
    error: str | None = None  # 작업 전체 실패 사유

    def status(self) -> str:
        if self.error:
            return "failed"
        if self.failed:
            return "partial" if self.ok > 0 else "failed"
        return "success"


@contextmanager
def job_run(job: str, session_factory: SessionFactory = SessionLocal) -> Iterator[JobResult]:
    """작업 시작/종료를 job_runs 에 남긴다. 예외는 기록 후 다시 던진다."""
    with session_factory() as s:
        row = JobRun(job=job, status="running")
        s.add(row)
        s.commit()
        run_id = row.id
    res = JobResult()
    try:
        yield res
    except Exception as e:
        res.error = f"{e.__class__.__name__}: {e}"
        raise
    finally:
        with session_factory() as s:
            row = s.get(JobRun, run_id)
            row.status = res.status()
            row.items_ok, row.items_failed = res.ok, len(res.failed)
            row.finished_at = datetime.now(UTC)
            msg = res.error or ("; ".join(res.failed) if res.failed else None)
            row.error = msg[:MAX_ERROR_CHARS] if msg else None
            s.commit()
        log.info("job %s: %s ok=%d failed=%d", job, res.status(), res.ok, len(res.failed))


def last_success_at(job: str, session_factory: SessionFactory = SessionLocal) -> datetime | None:
    with session_factory() as s:
        return s.scalar(
            select(JobRun.started_at)
            .where(JobRun.job == job, JobRun.status.in_(("success", "partial")))
            .order_by(JobRun.started_at.desc())
            .limit(1)
        )


def last_started_at(job: str, session_factory: SessionFactory = SessionLocal) -> datetime | None:
    """성공 여부와 무관한 마지막 시작 시각 (실패 시 주기마다 재시도하되 폭주하지 않게)."""
    with session_factory() as s:
        return s.scalar(
            select(JobRun.started_at).where(JobRun.job == job).order_by(JobRun.started_at.desc()).limit(1)
        )
