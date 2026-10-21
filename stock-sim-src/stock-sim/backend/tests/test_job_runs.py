"""job_runs 기록 (FR-CL-5)."""

import pytest
from sqlalchemy import select

from app.models import JobRun
from app.pipelines.common import JobResult, job_run, last_started_at, last_success_at


def _rows(sf):
    with sf() as s:
        return list(s.scalars(select(JobRun).order_by(JobRun.id)))


def test_success_row_has_timestamps_and_counts(sf):
    with job_run("j", sf) as r:
        r.ok = 30
    [row] = _rows(sf)
    assert (row.job, row.status, row.items_ok, row.items_failed, row.error) == ("j", "success", 30, 0, None)
    assert row.started_at and row.finished_at and row.finished_at >= row.started_at


def test_partial_when_some_items_fail_FR_CL_6(sf):
    with job_run("j", sf) as r:
        r.ok = 28
        r.failed = ["005930: ValueError", "000660: KeyError"]
    [row] = _rows(sf)
    assert (row.status, row.items_ok, row.items_failed) == ("partial", 28, 2)
    assert "005930: ValueError" in row.error and "000660: KeyError" in row.error


def test_failed_when_everything_fails(sf):
    with job_run("j", sf) as r:
        r.failed = ["a: X"]
    assert _rows(sf)[0].status == "failed"


def test_exception_is_recorded_and_reraised(sf):
    with pytest.raises(RuntimeError, match="boom"), job_run("j", sf):
        raise RuntimeError("boom")
    [row] = _rows(sf)
    assert row.status == "failed" and "RuntimeError: boom" in row.error and row.finished_at


def test_last_success_and_last_started(sf):
    assert last_success_at("j", sf) is None and last_started_at("j", sf) is None
    with job_run("j", sf):
        pass
    with pytest.raises(ValueError), job_run("j", sf):
        raise ValueError("x")
    assert last_success_at("j", sf) is not None  # 실패 건은 성공 시각에 포함되지 않는다
    assert last_started_at("j", sf) > last_success_at("j", sf)


def test_jobresult_status_matrix():
    assert JobResult().status() == "success"
    assert JobResult(ok=1, failed=["x"]).status() == "partial"
    assert JobResult(failed=["x"]).status() == "failed"
    assert JobResult(ok=5, error="e").status() == "failed"
