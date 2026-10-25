"""관리자 기능 (운영 화면): 판정, 현황, 사용자 관리, 작업 기록, 감사 기록.

관리자 판정 (DB 역할 열 없이 설정으로):
- 사용자 이메일이 ADMIN_EMAILS 에 있고,
- 그 이메일로 '인증된 이메일'을 주는 제공자(구글)에 연결돼 있고,
- 비밀번호가 없다(소셜로만 로그인).
이메일 가입은 이메일 소유를 확인하지 않으므로,
누가 관리자 이메일로 먼저 비밀번호 가입을 해도 관리자가 되지 못한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    AdminAudit,
    JobRun,
    OAuthAccount,
    Order,
    PaperAccount,
    Position,
    Prediction,
    Trade,
    User,
)
from app.services.market_data import KST

VERIFIED_EMAIL_PROVIDERS = ("google",)


class AdminError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def admin_emails() -> set[str]:
    return {e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()}


def is_admin(session: Session, user: User | None) -> bool:
    if user is None or not user.email or user.password_hash or user.suspended_at:
        return False
    email = user.email.lower()
    if email not in admin_emails():
        return False
    link = session.scalars(
        select(OAuthAccount.id).where(
            OAuthAccount.user_id == user.id,
            OAuthAccount.provider.in_(VERIFIED_EMAIL_PROVIDERS),
            func.lower(OAuthAccount.email) == email,
        )
    ).first()
    return link is not None


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(KST).isoformat() if dt else None


def _audit(
    session: Session, admin_id: int, action: str, target: int | None, detail: str | None = None
) -> None:
    session.add(AdminAudit(admin_user_id=admin_id, action=action, target_user_id=target, detail=detail))


# ---------------------------------------------------------------- 현황
def overview(session: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    today = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    def count(q) -> int:
        return int(session.scalar(q) or 0)

    providers = dict(
        session.execute(
            select(OAuthAccount.provider, func.count(func.distinct(OAuthAccount.user_id))).group_by(
                OAuthAccount.provider
            )
        ).all()
    )
    latest_pred = session.execute(
        select(Prediction.as_of_date, Prediction.model_version)
        .order_by(Prediction.as_of_date.desc(), Prediction.created_at.desc())
        .limit(1)
    ).first()
    # 작업별 마지막 실행
    last = select(JobRun.job, func.max(JobRun.started_at).label("at")).group_by(JobRun.job).subquery()
    jobs = session.scalars(
        select(JobRun)
        .join(last, (JobRun.job == last.c.job) & (JobRun.started_at == last.c.at))
        .order_by(JobRun.job)
    ).all()
    return {
        "users": {
            "total": count(select(func.count()).select_from(User)),
            "new_7d": count(select(func.count()).select_from(User).where(User.created_at >= week_ago)),
            "suspended": count(select(func.count()).select_from(User).where(User.suspended_at.is_not(None))),
            "password": count(select(func.count()).select_from(User).where(User.password_hash.is_not(None))),
            "google": int(providers.get("google", 0)),
            "naver": int(providers.get("naver", 0)),
        },
        "paper": {
            "user_accounts": count(
                select(func.count())
                .select_from(PaperAccount)
                .where(PaperAccount.type == "USER", PaperAccount.closed_at.is_(None))
            ),
            "ai_accounts": count(
                select(func.count())
                .select_from(PaperAccount)
                .where(PaperAccount.type == "AI", PaperAccount.closed_at.is_(None))
            ),
            "orders_today": count(select(func.count()).select_from(Order).where(Order.created_at >= today)),
            "trades_today": count(select(func.count()).select_from(Trade).where(Trade.filled_at >= today)),
            "open_orders": count(
                select(func.count()).select_from(Order).where(Order.status.in_(("PENDING", "PARTIAL")))
            ),
        },
        "model": {
            "serving": settings.model_version or None,
            "latest_as_of": latest_pred[0].isoformat() if latest_pred else None,
            "latest_version": latest_pred[1] if latest_pred else None,
            "ai_mode": settings.ai_mode,
        },
        "jobs": [job_dict(j) for j in jobs],
    }


def job_dict(j: JobRun) -> dict[str, Any]:
    return {
        "id": j.id,
        "job": j.job,
        "status": j.status,
        "started_at": _iso(j.started_at),
        "finished_at": _iso(j.finished_at),
        "items_ok": j.items_ok,
        "items_failed": j.items_failed,
        "error": (j.error or "")[:500] or None,
    }


def job_runs(session: Session, job: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    q = select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)
    if job:
        q = q.where(JobRun.job == job)
    return [job_dict(j) for j in session.scalars(q)]


# ---------------------------------------------------------------- 사용자
def _providers(session: Session, ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for uid, p in session.execute(
        select(OAuthAccount.user_id, OAuthAccount.provider).where(OAuthAccount.user_id.in_(ids))
    ):
        out.setdefault(uid, []).append(p)
    return out


def user_dict(session: Session, u: User, providers: list[str]) -> dict[str, Any]:
    methods = sorted(providers) + (["password"] if u.password_hash else [])
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "login_methods": methods,
        "created_at": _iso(u.created_at),
        "is_admin": is_admin(session, u),
        "suspended_at": _iso(u.suspended_at),
        "suspend_reason": u.suspend_reason,
        "hidden_from_ranking": bool(u.hidden_from_ranking),
    }


def list_users(session: Session, q: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    stmt = select(User)
    term = (q or "").strip()
    if term:
        like = f"%{term.lower()}%"
        conds = [func.lower(User.email).like(like), func.lower(User.display_name).like(like)]
        if term.isdigit():
            conds.append(User.id == int(term))
        stmt = stmt.where(or_(*conds))
    total = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    users = list(session.scalars(stmt.order_by(User.id.desc()).limit(limit).offset(offset)))
    prov = _providers(session, [u.id for u in users])
    accts: dict[int, int] = dict(
        session.execute(
            select(PaperAccount.user_id, func.count())
            .where(PaperAccount.user_id.in_([u.id for u in users]), PaperAccount.closed_at.is_(None))
            .group_by(PaperAccount.user_id)
        ).all()
    )
    rows = [
        {**user_dict(session, u, prov.get(u.id, [])), "open_accounts": int(accts.get(u.id, 0))} for u in users
    ]
    return {"rows": rows, "total": total}


def user_detail(session: Session, user_id: int, prices: dict[str, int]) -> dict[str, Any]:
    u = session.get(User, user_id)
    if u is None:
        raise AdminError(404, "USER_NOT_FOUND", "사용자를 찾을 수 없습니다.")
    accounts = []
    for a in session.scalars(
        select(PaperAccount).where(PaperAccount.user_id == u.id).order_by(PaperAccount.id)
    ):
        positions = list(
            session.scalars(select(Position).where(Position.account_id == a.id, Position.qty > 0))
        )
        value = a.cash + sum(p.qty * (prices.get(p.code) or int(p.avg_price)) for p in positions)
        accounts.append(
            {
                "id": a.id,
                "type": a.type,
                "initial_cash": a.initial_cash,
                "cash": a.cash,
                "value": value,
                "return_pct": (
                    round((value - a.initial_cash) / a.initial_cash * 100, 2) if a.initial_cash else None
                ),
                "positions": len(positions),
                "trades": int(
                    session.scalar(select(func.count()).select_from(Trade).where(Trade.account_id == a.id))
                    or 0
                ),
                "created_at": _iso(a.created_at),
                "closed_at": _iso(a.closed_at),
            }
        )
    ids = [a["id"] for a in accounts]
    orders = (
        session.scalars(
            select(Order).where(Order.account_id.in_(ids)).order_by(Order.created_at.desc()).limit(20)
        ).all()
        if ids
        else []
    )
    return {
        **user_dict(session, u, _providers(session, [u.id]).get(u.id, [])),
        "accounts": accounts,
        "recent_orders": [
            {
                "id": str(o.id),
                "account_id": o.account_id,
                "code": o.code,
                "side": o.side,
                "order_type": o.order_type,
                "qty": o.qty,
                "filled_qty": o.filled_qty,
                "limit_price": o.limit_price,
                "status": o.status,
                "created_at": _iso(o.created_at),
            }
            for o in orders
        ],
    }


def _target(session: Session, admin_id: int, user_id: int, *, lock: bool = True) -> User:
    u = session.get(User, user_id, with_for_update=lock)
    if u is None:
        raise AdminError(404, "USER_NOT_FOUND", "사용자를 찾을 수 없습니다.")
    return u


def suspend(session: Session, admin_id: int, user_id: int, reason: str | None) -> User:
    u = _target(session, admin_id, user_id)
    if u.id == admin_id or is_admin(session, u):
        raise AdminError(409, "CANNOT_SUSPEND_ADMIN", "관리자 계정은 정지할 수 없습니다.")
    reason = (reason or "").strip()[:200] or None
    if u.suspended_at is None:
        u.suspended_at = datetime.now(UTC)
        u.token_version += 1  # 로그인 중이던 기기도 바로 끊는다 (리프레시 무효)
    u.suspend_reason = reason
    _audit(session, admin_id, "suspend", u.id, reason)
    return u


def unsuspend(session: Session, admin_id: int, user_id: int) -> User:
    u = _target(session, admin_id, user_id)
    u.suspended_at = None
    u.suspend_reason = None
    _audit(session, admin_id, "unsuspend", u.id)
    return u


def force_logout(session: Session, admin_id: int, user_id: int) -> User:
    u = _target(session, admin_id, user_id)
    u.token_version += 1
    _audit(session, admin_id, "force_logout", u.id)
    return u


def set_ranking_hidden(session: Session, admin_id: int, user_id: int, hidden: bool) -> User:
    u = _target(session, admin_id, user_id)
    u.hidden_from_ranking = hidden
    _audit(session, admin_id, "hide_ranking" if hidden else "show_ranking", u.id)
    return u


def audit_log(session: Session, limit: int = 100) -> list[dict[str, Any]]:
    rows = session.scalars(select(AdminAudit).order_by(AdminAudit.id.desc()).limit(limit)).all()
    ids = {r.admin_user_id for r in rows} | {r.target_user_id for r in rows if r.target_user_id}
    names = {
        u.id: (u.display_name or u.email or f"#{u.id}")
        for u in session.scalars(select(User).where(User.id.in_(ids)))
    }
    return [
        {
            "id": r.id,
            "action": r.action,
            "admin": names.get(r.admin_user_id, f"#{r.admin_user_id}"),
            "target_user_id": r.target_user_id,
            "target": names.get(r.target_user_id) if r.target_user_id else None,
            "detail": r.detail,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]
