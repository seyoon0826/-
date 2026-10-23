"""인증 API (SPEC 10.3, FR-AU-1/2/3/5). 실제 DB, Redis 는 fakeredis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.common import get_store
from app.core import security
from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models import User
from app.services.market_store import QuoteStore

SECRET = "test-secret-" + "x" * 32
PW = "correct-horse-battery"


@pytest.fixture
def env(sf, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    server = fakeredis.FakeServer()

    def store():
        return QuoteStore(fakeredis.FakeAsyncRedis(server=server, decode_responses=True))

    def db():
        with sf() as s:
            yield s

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_store] = store
    with TestClient(app) as c:
        yield c, sf
    app.dependency_overrides.clear()


def signup(c, email="Kim@Example.com", pw=PW):
    return c.post("/api/v1/auth/signup", json={"email": email, "password": pw})


def bearer(r):
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


# ---------------------------------------------------------------- 가입/로그인 (FR-AU-1)
def test_signup_hashes_password_with_argon2_and_returns_tokens_FR_AU_1(env):
    c, sf = env
    r = signup(c)
    assert r.status_code == 201
    d = r.json()["data"]
    assert (
        d["token_type"] == "bearer" and d["expires_in"] == 30 * 60 and d["user"]["email"] == "kim@example.com"
    )
    with sf() as s:
        u = s.scalars(select(User)).one()
        assert u.password_hash.startswith("$argon2id$") and PW not in u.password_hash


def test_signup_validates_and_rejects_duplicate_case_insensitive(env):
    c, _ = env
    assert signup(c).status_code == 201
    r = signup(c, email="KIM@example.com")
    assert r.status_code == 409 and r.json()["error"]["code"] == "EMAIL_TAKEN"
    assert signup(c, email="not-an-email").status_code == 422
    assert signup(c, email="a@b.co", pw="short").json()["error"]["code"] == "INVALID_REQUEST"


def test_login_success_and_same_error_for_wrong_password_or_unknown_email(env):
    c, _ = env
    signup(c)
    ok = c.post("/api/v1/auth/login", json={"email": "kim@example.com", "password": PW})
    assert ok.status_code == 200
    assert c.get("/api/v1/auth/me", headers=bearer(ok)).json()["data"]["email"] == "kim@example.com"
    bad = c.post("/api/v1/auth/login", json={"email": "kim@example.com", "password": "wrong-password"})
    nobody = c.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": PW})
    assert bad.status_code == nobody.status_code == 401
    assert bad.json()["error"] == nobody.json()["error"]  # 계정 존재 여부를 알려주지 않는다


# ---------------------------------------------------------------- 토큰 (FR-AU-2)
def test_tokens_access_30min_and_refresh_14d_httponly_cookie_FR_AU_2(env):
    c, _ = env
    r = signup(c)
    claims = jwt.decode(r.json()["data"]["access_token"], SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 30 * 60 and claims["type"] == "access"
    cookie = r.headers["set-cookie"]
    assert "refresh_token=" in cookie and "HttpOnly" in cookie and "Path=/api/v1/auth" in cookie
    assert "Max-Age=1209600" in cookie and "samesite=lax" in cookie.lower()
    assert "refresh_token" not in r.json()["data"]  # 리프레시 토큰은 본문에 넣지 않는다


def test_refresh_issues_new_access_token_from_cookie(env):
    c, _ = env
    signup(c)
    r = c.post("/api/v1/auth/refresh")  # TestClient 가 쿠키를 보관해 보낸다
    assert r.status_code == 200 and r.json()["data"]["access_token"]
    assert c.get("/api/v1/auth/me", headers=bearer(r)).status_code == 200


def test_refresh_without_or_with_bad_cookie_is_401_and_clears_cookie(env):
    c, _ = env
    r = c.post("/api/v1/auth/refresh")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    c.cookies.set("refresh_token", "garbage", path="/api/v1/auth")
    r = c.post("/api/v1/auth/refresh")
    assert r.status_code == 401 and 'refresh_token=""' in r.headers.get("set-cookie", "")


def test_access_token_is_not_accepted_as_refresh_and_vice_versa(env):
    c, sf = env
    r = signup(c)
    access = r.json()["data"]["access_token"]
    c.cookies.clear()
    c.cookies.set("refresh_token", access, path="/api/v1/auth")
    assert c.post("/api/v1/auth/refresh").status_code == 401
    with sf() as s:
        uid = s.scalars(select(User.id)).one()
    refresh = security.create_refresh_token(uid, 0)
    assert c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"}).status_code == 401


def test_logout_revokes_all_refresh_tokens(env):
    c, _ = env
    signup(c)
    stolen = c.cookies.get("refresh_token")
    assert c.post("/api/v1/auth/logout").status_code == 200
    assert c.post("/api/v1/auth/refresh").status_code == 401  # 쿠키 삭제됨
    c.cookies.set("refresh_token", stolen, path="/api/v1/auth")
    assert c.post("/api/v1/auth/refresh").status_code == 401  # 복사해 둔 토큰도 무효 (token_version)
    c.cookies.clear()
    ok = c.post("/api/v1/auth/login", json={"email": "kim@example.com", "password": PW})
    assert ok.status_code == 200 and c.post("/api/v1/auth/refresh").status_code == 200  # 다시 로그인하면 정상


def test_expired_refresh_token_rejected(env):
    c, sf = env
    signup(c)
    with sf() as s:
        uid = s.scalars(select(User.id)).one()
    old = security.create_refresh_token(uid, 0, now=datetime.now(UTC) - timedelta(days=15))
    c.cookies.clear()
    c.cookies.set("refresh_token", old, path="/api/v1/auth")
    assert c.post("/api/v1/auth/refresh").status_code == 401


# ---------------------------------------------------------------- 비로그인 허용 / 로그인 필수 (FR-AU-3)
def test_public_endpoints_need_no_login_paper_needs_login_FR_AU_3(env):
    c, _ = env
    assert c.get("/api/v1/stocks").status_code == 200
    assert (
        c.get("/api/v1/model/performance").status_code != 401
    )  # 모델이 없으면 404 지만 로그인은 요구하지 않는다
    assert c.get("/api/v1/auth/me").status_code == 401
    assert c.get("/api/v1/paper/accounts/me").status_code == 401


# ---------------------------------------------------------------- 레이트 리밋 (FR-AU-5)
def test_login_rate_limited_10_per_minute_per_ip_FR_AU_5(env):
    c, _ = env
    codes = [
        c.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "whatever-1"}).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    r = c.post("/api/v1/auth/signup", json={"email": "new@example.com", "password": PW})
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"  # 가입도 같은 한도


def test_password_helpers():
    h = security.hash_password(PW)
    assert security.verify_password(h, PW) and not security.verify_password(h, PW + "x")
    assert not security.verify_password("not-a-hash", PW)
