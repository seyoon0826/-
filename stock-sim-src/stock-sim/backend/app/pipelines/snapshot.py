"""매일 포트폴리오 스냅샷 저장 (수익률 곡선, KOSPI 대비). 구현은 paper_jobs.snapshot_job."""

from app.pipelines.paper_jobs import snapshot_job

__all__ = ["snapshot_job"]
