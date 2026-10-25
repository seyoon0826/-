"""소셜 로그인 (네이버, 구글) — OAuth 2.0 인가 코드 흐름.

스펙 출처:
- 네이버 로그인 API (2026-10-25 확인, github.com/naver/naver-openapi-guide
  ko/login/api/api.md, ko/login/profile/profile.md)
  인가 nid.naver.com/oauth2.0/authorize (response_type, client_id, redirect_uri, state 필수),
  토큰 nid.naver.com/oauth2.0/token (grant_type=authorization_code, client_id, client_secret, code,
  state 필수.
  실패해도 200 에 error/error_description 으로 올 수 있어 access_token 유무로 판단),
  사용자 GET openapi.naver.com/v1/nid/me -> {resultcode:"00", response:{id, email, name, nickname}}.
  id 는 앱별 고유값. 이메일은 사용자가 바꾼 외부 주소일 수 있고 인증 여부 표시가 없어
  '인증 안 된 이메일'로 다룬다.
  문서에 PKCE 언급이 없어 state 만 쓴다.
- 구글 https://developers.google.com/identity/protocols/oauth2/web-server ,
  https://developers.google.com/identity/openid-connect/openid-connect
  인가 accounts.google.com/o/oauth2/v2/auth, 토큰 POST oauth2.googleapis.com/token,
  사용자 openidconnect.googleapis.com/v1/userinfo (sub, email, email_verified, name).
  디스커버리 문서의 code_challenge_methods_supported 에 S256 이 있어 PKCE 를 함께 쓴다.

계정 연결 규칙: 같은 (provider, subject) -> 같은 사용자. 처음 보는 소셜 계정은 제공자가 '인증된' 이메일이라고
알려준 경우에만 같은 이메일의 기존 사용자에 연결한다(인증 안 된 이메일로 남의 계정을 가로채지 못하게).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import OAuthAccount, User

TIMEOUT = httpx.Timeout(10.0)


class OAuthError(Exception):
    """사용자에게 보여줄 수 있는 짧은 코드 (예: token_exchange_failed)."""


@dataclass(frozen=True)
class Profile:
    provider: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


# 제공자 -> (client_id, client_secret) 설정 이름
_KEYS = {
    "naver": ("naver_login_client_id", "naver_login_client_secret"),
    "google": ("google_client_id", "google_client_secret"),
}


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str
    pkce: bool

    @property
    def client_id(self) -> str:
        return str(getattr(settings, _KEYS[self.name][0]))

    @property
    def client_secret(self) -> str:
        return str(getattr(settings, _KEYS[self.name][1]))

    @property
    def enabled(self) -> bool:
        return bool(self.client_id)

    @property
    def redirect_uri(self) -> str:
        return f"{settings.api_public_url.rstrip('/')}/api/v1/auth/oauth/{self.name}/callback"

    def authorize(self, state: str, code_challenge: str | None) -> str:
        q: dict[str, str] = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
        }
        if self.scope:
            q["scope"] = self.scope
        if self.name == "google":
            q["prompt"] = "select_account"
        if self.pkce and code_challenge:
            q["code_challenge"] = code_challenge
            q["code_challenge_method"] = "S256"
        return f"{self.authorize_url}?{urlencode(q)}"

    def exchange(self, code: str, code_verifier: str | None, state: str | None = None) -> str:
        """인가 코드 -> 액세스 토큰."""
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "code": code,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        if self.pkce and code_verifier:
            data["code_verifier"] = code_verifier
        if self.name == "naver" and state:
            data["state"] = state  # 네이버는 토큰 요청에도 state 가 필수
        body = _post_form(self.token_url, data)
        token = body.get("access_token")
        if not token:
            raise OAuthError("token_exchange_failed")
        return str(token)

    def profile(self, access_token: str) -> Profile:
        u = _get_json(self.userinfo_url, access_token)
        if self.name == "naver":
            r = u.get("response") or {}
            if u.get("resultcode") != "00" or not r.get("id"):
                raise OAuthError("profile_failed")
            # 인증 여부를 알려주지 않으므로 기존 계정에 자동 연결하지 않는다
            return Profile("naver", str(r["id"]), r.get("email"), False, r.get("nickname") or r.get("name"))
        if not u.get("sub"):
            raise OAuthError("profile_failed")
        return Profile("google", str(u["sub"]), u.get("email"), bool(u.get("email_verified")), u.get("name"))


PROVIDERS: dict[str, Provider] = {
    "naver": Provider(
        "naver",
        "네이버",
        "https://nid.naver.com/oauth2.0/authorize",
        "https://nid.naver.com/oauth2.0/token",
        "https://openapi.naver.com/v1/nid/me",
        "",  # 제공 항목은 네이버 개발자 센터 앱 설정을 따른다
        False,
    ),
    "google": Provider(
        "google",
        "Google",
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "https://openidconnect.googleapis.com/v1/userinfo",
        "openid email profile",
        True,
    ),
}


# ---------------------------------------------------------------- HTTP (테스트에서 교체)
def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        r = httpx.post(url, data=data, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise OAuthError("provider_unreachable") from e
    if r.status_code != 200:
        raise OAuthError("token_exchange_failed")
    return dict(r.json())


def _get_json(url: str, access_token: str) -> dict[str, Any]:
    try:
        r = httpx.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise OAuthError("provider_unreachable") from e
    if r.status_code != 200:
        raise OAuthError("profile_failed")
    return dict(r.json())


# ---------------------------------------------------------------- PKCE / state
def new_state() -> str:
    return secrets.token_urlsafe(24)


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge[S256])"""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def safe_next(path: str | None, default: str = "/paper") -> str:
    """로그인 후 돌아갈 경로. 같은 사이트의 상대 경로만 허용 (오픈 리다이렉트 방지)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return default
    return path


# ---------------------------------------------------------------- 사용자 찾기/만들기
def find_or_create_user(session: Session, p: Profile) -> User:
    link = session.scalars(
        select(OAuthAccount).where(OAuthAccount.provider == p.provider, OAuthAccount.subject == p.subject)
    ).first()
    if link is not None:
        user = session.get(User, link.user_id)
        assert user is not None
        return user
    email = p.email.strip().lower() if p.email else None
    user = None
    if email and p.email_verified:
        user = session.scalars(select(User).where(func.lower(User.email) == email)).first()
    if user is None:
        taken = email is not None and session.scalar(
            select(func.count()).where(func.lower(User.email) == email)
        )
        user = User(
            email=email if (email and p.email_verified and not taken) else None,
            password_hash=None,
            display_name=(p.name or "")[:100] or None,
        )
        session.add(user)
        session.flush()
    session.add(OAuthAccount(user_id=user.id, provider=p.provider, subject=p.subject, email=email))
    try:
        session.flush()
    except IntegrityError:  # 같은 소셜 계정으로 동시에 로그인: 먼저 만든 연결을 쓴다
        session.rollback()
        return find_or_create_user(session, p)
    return user
