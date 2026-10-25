"""관리자 기능: 판정 규칙, 접근 제한, 사용자 정지/해제, 강제 로그아웃, 감사 기록."""

from __future__ import annotations

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.common import get_store
from app.core.config import settings
from app.core.db import get_db
from app.core.security import create_access_token, create_refresh_token
from app.main import app
from app.models import AdminAudit, OAuthAccount, User
from app.services import admin
from app.services.market_store import QuoteStore

SECRET = "test-secret-" + "x" * 32
PW = "correct-horse-battery"
ADMIN = "boss@gmail.com"


@pytest.fixture
def env(sf, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    monkeypatch.setattr(settings, "admin_emails", f" {ADMIN.upper()} , second@school.kr")
    server = fakeredis.FakeServer()

    def db():
        with sf() as s:
            yield s

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_store] = lambda: QuoteStore(
        fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    )
    with TestClient(app) as c:
        yield c, sf
    app.dependency_overrides.clear()


def make_user(sf, email, *, provider="google", password=None, name=None) -> int:
    with sf() as s:
        u = User(email=email, password_hash=password, display_name=name)
        s.add(u)
        s.flush()
        if provider:
            s.add(OAuthAccount(user_id=u.id, provider=provider, subject=f"{provider}-{u.id}", email=email))
        s.commit()
        return u.id


def auth(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(uid)}"}


# ---------------------------------------------------------------- 판정
def test_admin_requires_listed_email_verified_provider_and_no_password(env):
    _, sf = env
    boss = make_user(sf, ADMIN)
    pw = make_user(sf, "second@school.kr", provider="google", password="hash")  # 비밀번호가 있으면 아니다
    naver = make_user(sf, "x@naver.com", provider="naver")
    other = make_user(sf, "someone@gmail.com")
    with sf() as s:
        assert admin.is_admin(s, s.get(User, boss))
        assert not admin.is_admin(s, s.get(User, pw))
        assert not admin.is_admin(s, s.get(User, naver))
        assert not admin.is_admin(s, s.get(User, other))


def test_password_signup_with_admin_email_is_not_admin(env):
    c, _ = env
    r = c.post("/api/v1/auth/signup", json={"email": ADMIN, "password": PW})
    assert r.status_code == 201 and r.json()["data"]["user"]["is_admin"] is False
    h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    assert c.get("/api/v1/admin/overview", headers=h).status_code == 403


def test_admin_endpoints_need_admin(env):
    c, sf = env
    boss = make_user(sf, ADMIN)
    other = make_user(sf, "someone@gmail.com")
    assert c.get("/api/v1/admin/overview").status_code == 401
    assert c.get("/api/v1/admin/users", headers=auth(other)).status_code == 403
    assert c.get("/api/v1/auth/me", headers=auth(other)).json()["data"]["is_admin"] is False
    assert c.get("/api/v1/auth/me", headers=auth(boss)).json()["data"]["is_admin"] is True
    ov = c.get("/api/v1/admin/overview", headers=auth(boss))
    assert ov.status_code == 200
    data = ov.json()["data"]
    assert data["users"]["total"] == 2 and data["users"]["google"] == 2 and data["users"]["password"] == 0
    assert {"orders_today", "trades_today", "open_orders"} <= data["paper"].keys()


def test_user_list_search_and_detail(env):
    c, sf = env
    boss = make_user(sf, ADMIN, name="관리자")
    kim = make_user(sf, "kim@gmail.com", name="김주식")
    r = c.get("/api/v1/admin/users", params={"q": "김주"}, headers=auth(boss)).json()["data"]
    assert r["total"] == 1 and r["rows"][0]["id"] == kim and r["rows"][0]["login_methods"] == ["google"]
    assert (
        c.get("/api/v1/admin/users", params={"q": str(kim)}, headers=auth(boss)).json()["data"]["total"] == 1
    )
    d = c.get(f"/api/v1/admin/users/{kim}", headers=auth(boss)).json()["data"]
    assert d["accounts"] == [] and d["recent_orders"] == [] and d["is_admin"] is False
    assert c.get("/api/v1/admin/users/99999", headers=auth(boss)).status_code == 404


# ---------------------------------------------------------------- 조치
def test_suspend_blocks_api_refresh_and_login_then_unsuspend(env):
    c, sf = env
    boss = make_user(sf, ADMIN)
    signed = c.post("/api/v1/auth/signup", json={"email": "bad@example.com", "password": PW}).json()["data"]
    bad = signed["user"]["id"]
    h = {"Authorization": f"Bearer {signed['access_token']}"}
    c.cookies.clear()

    r = c.post(f"/api/v1/admin/users/{bad}/suspend", json={"reason": "도배"}, headers=auth(boss))
    assert r.status_code == 200 and r.json()["data"]["suspend_reason"] == "도배"
    assert c.get("/api/v1/auth/me", headers=h).status_code == 403  # 남아 있던 액세스 토큰도 막힌다
    login = c.post("/api/v1/auth/login", json={"email": "bad@example.com", "password": PW})
    assert login.status_code == 403 and login.json()["error"]["code"] == "ACCOUNT_SUSPENDED"
    with sf() as s:
        u = s.get(User, bad)
        assert u.suspended_at is not None and u.token_version == 1  # 리프레시 토큰 무효
        stale = create_refresh_token(bad, 0)
    c.cookies.set("refresh_token", stale, path="/api/v1/auth")
    assert c.post("/api/v1/auth/refresh").status_code == 401

    assert c.post(f"/api/v1/admin/users/{bad}/unsuspend", headers=auth(boss)).status_code == 200
    c.cookies.clear()
    assert c.post("/api/v1/auth/login", json={"email": "bad@example.com", "password": PW}).status_code == 200
    with sf() as s:
        actions = [a.action for a in s.scalars(select(AdminAudit).order_by(AdminAudit.id))]
    assert actions == ["suspend", "unsuspend"]
    log = c.get("/api/v1/admin/audit", headers=auth(boss)).json()["data"]
    assert log[0]["action"] == "unsuspend" and log[1]["detail"] == "도배"


def test_cannot_suspend_admin_or_self(env):
    c, sf = env
    boss = make_user(sf, ADMIN)
    second = make_user(sf, "second@school.kr")
    for target in (boss, second):
        r = c.post(f"/api/v1/admin/users/{target}/suspend", json={}, headers=auth(boss))
        assert r.status_code == 409 and r.json()["error"]["code"] == "CANNOT_SUSPEND_ADMIN"


def test_force_logout_revokes_refresh_tokens(env):
    c, sf = env
    boss = make_user(sf, ADMIN)
    kim = make_user(sf, "kim@gmail.com")
    old = create_refresh_token(kim, 0)
    assert c.post(f"/api/v1/admin/users/{kim}/logout", headers=auth(boss)).status_code == 200
    c.cookies.set("refresh_token", old, path="/api/v1/auth")
    assert c.post("/api/v1/auth/refresh").status_code == 401


def test_ranking_toggle_and_jobs(env):
    c, sf = env
    boss = make_user(sf, ADMIN)
    kim = make_user(sf, "kim@gmail.com")
    r = c.post(f"/api/v1/admin/users/{kim}/ranking", json={"hidden": True}, headers=auth(boss))
    assert r.status_code == 200 and r.json()["data"]["hidden_from_ranking"] is True
    assert c.get("/api/v1/admin/jobs", headers=auth(boss)).json()["data"] == []
