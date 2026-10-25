"""관리자 API (/api/v1/admin). 모든 엔드포인트는 관리자만 (services/admin.is_admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import ApiError, envelope
from app.api.deps import current_admin_id
from app.core.db import get_db
from app.models import Position
from app.services import admin as svc
from app.services.paper.market import last_close

router = APIRouter()


class SuspendBody(BaseModel):
    reason: str | None = Field(None, max_length=200)


class RankingBody(BaseModel):
    hidden: bool


async def _run(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except svc.AdminError as e:
        raise ApiError(e.status, e.code, e.message) from None


@router.get("/overview")
async def overview(admin_id: int = Depends(current_admin_id), db: Session = Depends(get_db)):
    return envelope(await _run(svc.overview, db))


@router.get("/users")
async def users(
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin_id: int = Depends(current_admin_id),
    db: Session = Depends(get_db),
):
    return envelope(await _run(svc.list_users, db, q, limit, offset))


@router.get("/users/{user_id}")
async def user_detail(user_id: int, admin_id: int = Depends(current_admin_id), db: Session = Depends(get_db)):
    def work():
        codes = list(db.scalars(select(Position.code).where(Position.qty > 0).distinct()))
        prices = {c: p for c in codes if (p := last_close(db, c))}
        return svc.user_detail(db, user_id, prices)

    return envelope(await _run(work), {"valuation": "최근 종가"})


def _act(db: Session, fn, *args):
    u = fn(db, *args)
    db.commit()
    return svc.user_dict(db, u, [])


@router.post("/users/{user_id}/suspend")
async def suspend(
    user_id: int, body: SuspendBody, admin_id: int = Depends(current_admin_id), db: Session = Depends(get_db)
):
    return envelope(await _run(_act, db, svc.suspend, admin_id, user_id, body.reason))


@router.post("/users/{user_id}/unsuspend")
async def unsuspend(user_id: int, admin_id: int = Depends(current_admin_id), db: Session = Depends(get_db)):
    return envelope(await _run(_act, db, svc.unsuspend, admin_id, user_id))


@router.post("/users/{user_id}/logout")
async def force_logout(
    user_id: int, admin_id: int = Depends(current_admin_id), db: Session = Depends(get_db)
):
    """그 사용자의 모든 기기 로그아웃 (리프레시 토큰 무효. 액세스 토큰은 길어야 30분 뒤 만료)."""
    return envelope(await _run(_act, db, svc.force_logout, admin_id, user_id))


@router.post("/users/{user_id}/ranking")
async def ranking(
    user_id: int, body: RankingBody, admin_id: int = Depends(current_admin_id), db: Session = Depends(get_db)
):
    return envelope(await _run(_act, db, svc.set_ranking_hidden, admin_id, user_id, body.hidden))


@router.get("/jobs")
async def jobs(
    job: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    admin_id: int = Depends(current_admin_id),
    db: Session = Depends(get_db),
):
    return envelope(await _run(svc.job_runs, db, job, limit))


@router.get("/audit")
async def audit(
    limit: int = Query(100, ge=1, le=500),
    admin_id: int = Depends(current_admin_id),
    db: Session = Depends(get_db),
):
    return envelope(await _run(svc.audit_log, db, limit))
