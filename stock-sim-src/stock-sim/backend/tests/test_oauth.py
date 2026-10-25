"""소셜 로그인 (카카오, 구글). 제공자 HTTP 는 가짜로 바꾼다 (네트워크 없이)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.common import get_store
from app.core import security
from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models import OAuthAccount, User
from app.services import oauth
from app.services.market_store import QuoteStore

SECRET = "test-secret-" + "x" * 32


@pytest.fixture
def env(sf, monkeypatch):
    for k, v in {
        "jwt_secret": SECRET,
        "kakao_rest_api_key": "kakao-key",
        "kakao_client_secret": "kakao-secret",
        "google_client_id": "google-id",
        "google_client_secret": "google-secret",
        "api_public_url": "http://api.test",
        "frontend_url": "http://web.test",
    }.items():
        monkeypatch.setattr(settings, k, v)
    server = fakeredis.FakeServer()

    def db():
        with sf() as s:
            yield s

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_store] = lambda: QuoteStore(
        fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    )
    with TestClient(app, follow_redirects=False) as c:
        yield c, sf, monkeypatch
    app.dependency_overrides.clear()


def fake_provider(monkeypatch, profile: dict, calls: list | None = None):
    def post(url, data):
        if calls is not None:
            calls.append(("token", url, dict(data)))
        return {"access_token": "provider-token", "token_type": "bearer"}

    def get(url, token):
        assert token == "provider-token"
        return profile

    monkeypatch.setattr(oauth, "_post_form", post)
    monkeypatch.setattr(oauth, "_get_json", get)


def start(c, provider="kakao", next_="/paper"):
    r = c.get(f"/api/v1/auth/oauth/{provider}/start", params={"next": next_})
    assert r.status_code == 302
    return r, parse_qs(urlparse(r.headers["location"]).query)


def login(c, provider, profile_patch, monkeypatch, profile, next_="/paper", calls=None):
    fake_provider(monkeypatch, profile, calls)
    _, q = start(c, provider, next_)
    return c.get(
        f"/api/v1/auth/oauth/{provider}/callback", params={"code": "auth-code", "state": q["state"][0]}
    )


KAKAO = {
    "id": 123456789,
    "kakao_account": {
        "email": "Kim@Kakao.com",
        "is_email_verified": True,
        "is_email_valid": True,
        "profile": {"nickname": "김주식"},
    },
}
GOOGLE = {"sub": "g-42", "email": "lee@gmail.com", "email_verified": True, "name": "Lee"}


# ---------------------------------------------------------------- 시작 (인가 요청)
def test_providers_list_only_configured(env):
    c, _, mp = env
    assert [p["id"] for p in c.get("/api/v1/auth/oauth/providers").json()["data"]] == ["kakao", "google"]
    mp.setattr(settings, "google_client_id", "")
    assert [p["id"] for p in c.get("/api/v1/auth/oauth/providers").json()["data"]] == ["kakao"]
    assert c.get("/api/v1/auth/oauth/google/start").status_code == 404
    assert c.get("/api/v1/auth/oauth/naver/start").status_code == 404


def test_kakao_start_redirects_with_client_id_redirect_uri_and_state(env):
    c, _, _ = env
    r, q = start(c, "kakao")
    assert r.headers["location"].startswith("https://kauth.kakao.com/oauth/authorize?")
    assert q["client_id"] == ["kakao-key"] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["http://api.test/api/v1/auth/oauth/kakao/callback"]
    assert len(q["state"][0]) >= 20 and "code_challenge" not in q
    cookie = r.headers["set-cookie"]
    assert "oauth_state=" in cookie and "HttpOnly" in cookie and "Path=/api/v1/auth/oauth" in cookie


def test_google_start_uses_pkce_s256_and_openid_scope(env):
    c, _, _ = env
    r, q = start(c, "google")
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["scope"] == ["openid email profile"] and q["code_challenge_method"] == ["S256"]
    assert len(q["code_challenge"][0]) == 43  # base64url(sha256) 패딩 없음


# ---------------------------------------------------------------- 콜백
def test_kakao_callback_creates_user_sets_refresh_cookie_and_redirects(env):
    c, sf, mp = env
    calls: list = []
    r = login(c, "kakao", None, mp, KAKAO, next_="/paper?code=005930", calls=calls)
    assert r.status_code == 302 and r.headers["location"] == "http://web.test/paper?code=005930&login=ok"
    assert "refresh_token=" in r.headers.get("set-cookie", "")
    token_req = calls[0][2]
    assert calls[0][1] == "https://kauth.kakao.com/oauth/token"
    assert token_req == {
        "grant_type": "authorization_code",
        "client_id": "kakao-key",
        "redirect_uri": "http://api.test/api/v1/auth/oauth/kakao/callback",
        "code": "auth-code",
        "client_secret": "kakao-secret",
    }
    with sf() as s:
        u = s.scalars(select(User)).one()
        assert u.email == "kim@kakao.com" and u.display_name == "김주식" and u.password_hash is None
        assert s.scalars(select(OAuthAccount)).one().subject == "123456789"
    me = c.post("/api/v1/auth/refresh")  # 프론트가 하는 것처럼 쿠키로 액세스 토큰 받기
    assert me.status_code == 200 and me.json()["data"]["user"]["display_name"] == "김주식"


def test_google_callback_sends_code_verifier_matching_challenge(env):
    import base64
    import hashlib

    c, _, mp = env
    calls: list = []
    fake_provider(mp, GOOGLE, calls)
    _, q = start(c, "google")
    c.get("/api/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": q["state"][0]})
    verifier = calls[0][2]["code_verifier"]
    assert (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        == q["code_challenge"][0]
    )


def test_same_social_account_logs_into_same_user(env):
    c, sf, mp = env
    login(c, "kakao", None, mp, KAKAO)
    c.cookies.clear()
    login(c, "kakao", None, mp, KAKAO)
    with sf() as s:
        assert len(s.scalars(select(User)).all()) == 1 and len(s.scalars(select(OAuthAccount)).all()) == 1


def test_verified_email_links_existing_password_account(env):
    c, sf, mp = env
    c.post("/api/v1/auth/signup", json={"email": "lee@gmail.com", "password": "correct-horse-battery"})
    c.cookies.clear()
    login(c, "google", None, mp, GOOGLE)
    with sf() as s:
        users = s.scalars(select(User)).all()
        assert len(users) == 1 and s.scalars(select(OAuthAccount)).one().user_id == users[0].id


def test_unverified_email_does_not_take_over_existing_account(env):
    c, sf, mp = env
    c.post("/api/v1/auth/signup", json={"email": "kim@kakao.com", "password": "correct-horse-battery"})
    c.cookies.clear()
    unverified = {**KAKAO, "kakao_account": {**KAKAO["kakao_account"], "is_email_verified": False}}
    login(c, "kakao", None, mp, unverified)
    with sf() as s:
        users = s.scalars(select(User).order_by(User.id)).all()
        assert len(users) == 2 and users[1].email is None  # 새 사용자, 이메일은 비워 둔다
        assert s.scalars(select(OAuthAccount)).one().user_id == users[1].id


def test_kakao_without_email_consent_creates_user_without_email(env):
    c, sf, mp = env
    login(c, "kakao", None, mp, {"id": 7, "kakao_account": {"profile": {"nickname": "익명"}}})
    with sf() as s:
        u = s.scalars(select(User)).one()
        assert u.email is None and u.display_name == "익명"


def test_state_mismatch_or_missing_cookie_is_rejected(env):
    c, sf, mp = env
    fake_provider(mp, KAKAO)
    start(c, "kakao")
    r = c.get("/api/v1/auth/oauth/kakao/callback", params={"code": "x", "state": "forged"})
    assert r.status_code == 302 and "login_error=state_mismatch" in r.headers["location"]
    c.cookies.clear()
    r = c.get("/api/v1/auth/oauth/kakao/callback", params={"code": "x", "state": "anything"})
    assert "login_error=state_expired" in r.headers["location"]
    with sf() as s:
        assert s.scalars(select(User)).all() == []


def test_state_cookie_for_other_provider_is_rejected(env):
    c, _, mp = env
    fake_provider(mp, GOOGLE)
    _, q = start(c, "kakao")
    r = c.get("/api/v1/auth/oauth/google/callback", params={"code": "x", "state": q["state"][0]})
    assert "login_error=state_mismatch" in r.headers["location"]


def test_user_cancel_and_provider_failure_redirect_with_error(env):
    c, sf, mp = env
    start(c, "kakao")
    r = c.get("/api/v1/auth/oauth/kakao/callback", params={"error": "access_denied"})
    assert "login_error=cancelled" in r.headers["location"]

    def boom(url, data):
        raise oauth.OAuthError("token_exchange_failed")

    mp.setattr(oauth, "_post_form", boom)
    _, q = start(c, "kakao")
    r = c.get("/api/v1/auth/oauth/kakao/callback", params={"code": "x", "state": q["state"][0]})
    assert "login_error=token_exchange_failed" in r.headers["location"]
    assert "refresh_token=" not in r.headers.get("set-cookie", "")


def test_next_path_cannot_redirect_off_site(env):
    for bad in ("//evil.example", "https://evil.example", "/\\\\evil.example", "paper"):
        assert oauth.safe_next(bad) == "/paper"
    assert oauth.safe_next("/predict/005930") == "/predict/005930"
    c, _, mp = env
    r = login(c, "kakao", None, mp, KAKAO, next_="//evil.example")
    assert r.headers["location"] == "http://web.test/paper?login=ok"


def test_social_only_user_cannot_password_login(env):
    c, _, mp = env
    login(c, "kakao", None, mp, KAKAO)
    r = c.post("/api/v1/auth/login", json={"email": "kim@kakao.com", "password": "anything-at-all"})
    assert r.status_code == 401
    assert not security.verify_password(None, "x")
