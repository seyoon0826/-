"""인증 API (SPEC 10.3, FR-AU-1/2/5): 가입, 로그인, 토큰 갱신, 로그아웃, 내 정보.

응답 data = {access_token, token_type, expires_in, user}. 리프레시 토큰은 httpOnly 쿠키로만 준다
(자바스크립트가 읽을 수 없게). 쿠키 경로를 /api/v1/auth 로 좁혀 다른 API 요청에는 실리지 않는다.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import ApiError, envelope, get_store
from app.api.deps import current_user_id
from app.core.config import settings
from app.core.db import get_db
from app.core.security import (
    InvalidToken,
    create_access_token,
    create_refresh_token,
    decode_oauth_state,
    decode_refresh_token,
    encode_oauth_state,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models import User
from app.services import oauth
from app.services.market_data import KST
from app.services.market_store import QuoteStore

log = logging.getLogger(__name__)
router = APIRouter()

COOKIE = "refresh_token"
COOKIE_PATH = "/api/v1/auth"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN, PASSWORD_MAX = 8, 128
# 없는 이메일과 틀린 비밀번호의 응답 시간을 비슷하게 맞추기 위한 더미 해시 (계정 존재 여부 노출 방지)
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class Credentials(BaseModel):
    email: str
    password: str


def _user_dict(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "created_at": u.created_at.astimezone(KST).isoformat(),
    }


def _issue(response: Response, u: User) -> dict[str, Any]:
    response.set_cookie(
        COOKIE,
        create_refresh_token(u.id, u.token_version),
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",
        path=COOKIE_PATH,
    )
    return {
        "access_token": create_access_token(u.id),
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
        "user": _user_dict(u),
    }


def _clear(response: Response) -> None:
    response.delete_cookie(
        COOKIE, path=COOKIE_PATH, httponly=True, samesite="lax", secure=settings.refresh_cookie_secure
    )


async def rate_limit(request: Request, store: QuoteStore = Depends(get_store)) -> None:
    """IP 당 분당 N회 (FR-AU-5). Redis 고정 창 카운터. Redis 장애 시에는 막지 않는다(로그만)."""
    ip = request.client.host if request.client else "unknown"
    key = f"rl:auth:{ip}:{int(time.time() // 60)}"
    try:
        n = await store.r.incr(key)
        if n == 1:
            await store.r.expire(key, 70)
    except Exception as e:  # noqa: BLE001
        log.warning("레이트 리밋 확인 실패(통과시킴): %s", e.__class__.__name__)
        return
    if n > settings.login_rate_per_min:
        raise ApiError(429, "RATE_LIMITED", "요청이 너무 많습니다. 1분 뒤 다시 시도하세요.")


def _normalize(body: Credentials) -> tuple[str, str]:
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 255:
        raise ApiError(422, "INVALID_REQUEST", "이메일 형식이 올바르지 않습니다.")
    if not PASSWORD_MIN <= len(body.password) <= PASSWORD_MAX:
        raise ApiError(422, "INVALID_REQUEST", f"비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자여야 합니다.")
    return email, body.password


def _find(db: Session, email: str) -> User | None:
    return db.scalars(select(User).where(func.lower(User.email) == email)).first()


@router.post("/signup", status_code=201, dependencies=[Depends(rate_limit)])
async def signup(body: Credentials, response: Response, db: Session = Depends(get_db)):
    email, password = _normalize(body)

    def work() -> dict[str, Any]:
        if _find(db, email) is not None:
            raise ApiError(409, "EMAIL_TAKEN", "이미 가입된 이메일입니다.")
        u = User(email=email, password_hash=hash_password(password))
        db.add(u)
        try:
            db.commit()
        except IntegrityError:  # 동시 가입
            db.rollback()
            raise ApiError(409, "EMAIL_TAKEN", "이미 가입된 이메일입니다.") from None
        db.refresh(u)
        return _issue(response, u)

    return envelope(await run_in_threadpool(work))


@router.post("/login", dependencies=[Depends(rate_limit)])
async def login(body: Credentials, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()

    def work() -> dict[str, Any]:
        u = _find(db, email)
        if u is None or u.password_hash is None:  # 소셜로만 가입한 계정은 비밀번호 로그인 불가
            verify_password(_DUMMY_HASH, body.password)
            raise ApiError(401, "UNAUTHORIZED", "이메일 또는 비밀번호가 올바르지 않습니다.")
        if not verify_password(u.password_hash, body.password):
            raise ApiError(401, "UNAUTHORIZED", "이메일 또는 비밀번호가 올바르지 않습니다.")
        if needs_rehash(u.password_hash):
            u.password_hash = hash_password(body.password)
            db.commit()
        return _issue(response, u)

    return envelope(await run_in_threadpool(work))


def _from_refresh(db: Session, token: str | None) -> User:
    if not token:
        raise ApiError(401, "UNAUTHORIZED", "로그인이 필요합니다.")
    try:
        uid, ver = decode_refresh_token(token)
    except InvalidToken:
        raise ApiError(401, "UNAUTHORIZED", "로그인이 만료됐습니다. 다시 로그인하세요.") from None
    u = db.get(User, uid)
    if u is None or u.token_version != ver:
        raise ApiError(401, "UNAUTHORIZED", "로그인이 만료됐습니다. 다시 로그인하세요.")
    return u


@router.post("/refresh")
async def refresh(
    response: Response, db: Session = Depends(get_db), refresh_token: str | None = Cookie(None)
):
    """새 액세스 토큰 + 리프레시 쿠키 재발급(만료일 연장)."""

    def work() -> dict[str, Any]:
        return _issue(response, _from_refresh(db, refresh_token))

    try:
        return envelope(await run_in_threadpool(work))
    except ApiError as e:  # 무효 쿠키는 지워서 브라우저가 계속 보내지 않게 한다
        err = JSONResponse(
            status_code=e.status_code, content={"error": {"code": e.code, "message": e.message}}
        )
        _clear(err)
        return err


@router.post("/logout")
async def logout(response: Response, db: Session = Depends(get_db), refresh_token: str | None = Cookie(None)):
    """쿠키를 지우고, 유효한 리프레시 토큰이면 그 사용자의 모든 리프레시 토큰을 무효로 만든다."""

    def work() -> None:
        try:
            u = _from_refresh(db, refresh_token)
        except ApiError:
            return
        u.token_version += 1
        db.commit()

    await run_in_threadpool(work)
    _clear(response)
    return envelope({"ok": True})


@router.get("/me")
async def me(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    u = await run_in_threadpool(db.get, User, user_id)
    assert u is not None
    return envelope(_user_dict(u))


# ---------------------------------------------------------------- 소셜 로그인 (카카오, 구글)
OAUTH_COOKIE = "oauth_state"
OAUTH_COOKIE_PATH = "/api/v1/auth/oauth"
OAUTH_STATE_SECONDS = 600


@router.get("/oauth/providers")
async def oauth_providers():
    """설정된(키가 있는) 소셜 로그인 제공자. 프론트는 이 목록의 버튼만 보여준다."""
    return envelope([{"id": p.name, "label": p.label} for p in oauth.PROVIDERS.values() if p.enabled])


def _provider(name: str) -> oauth.Provider:
    p = oauth.PROVIDERS.get(name)
    if p is None or not p.enabled:
        raise ApiError(404, "PROVIDER_NOT_AVAILABLE", "지원하지 않거나 설정되지 않은 로그인 방식입니다.")
    return p


@router.get("/oauth/{provider}/start", dependencies=[Depends(rate_limit)])
async def oauth_start(provider: str, next: str | None = None):
    """제공자 로그인 화면으로 보낸다.
    state(CSRF)와 PKCE verifier 는 서명한 httpOnly 쿠키에 담아 콜백에서 확인한다."""
    p = _provider(provider)
    state = oauth.new_state()
    verifier, challenge = oauth.pkce_pair() if p.pkce else (None, None)
    resp = RedirectResponse(p.authorize(state, challenge), status_code=302)
    resp.set_cookie(
        OAUTH_COOKIE,
        encode_oauth_state(
            {"provider": p.name, "state": state, "verifier": verifier, "next": oauth.safe_next(next)},
            OAUTH_STATE_SECONDS,
        ),
        max_age=OAUTH_STATE_SECONDS,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",  # 제공자에서 돌아오는 최상위 GET 이동에는 쿠키가 실린다
        path=OAUTH_COOKIE_PATH,
    )
    return resp


def _back_to_front(path: str, error: str | None = None) -> RedirectResponse:
    url = settings.frontend_url.rstrip("/") + path
    if error:
        url += ("&" if "?" in url else "?") + f"login_error={error}"
    resp = RedirectResponse(url, status_code=302)
    resp.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    return resp


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    oauth_state: str | None = Cookie(None),
):
    """제공자에서 돌아온 뒤: state 확인 -> 코드 교환 -> 사용자 찾기/만들기 -> 리프레시 쿠키 -> 프론트로.
    프론트는 기존처럼 /auth/refresh 로 액세스 토큰을 받는다."""
    try:
        saved = decode_oauth_state(oauth_state or "")
    except InvalidToken:
        return _back_to_front("/paper", "state_expired")
    nxt = oauth.safe_next(saved.get("next"))
    if error:  # 사용자가 동의를 취소한 경우 등
        return _back_to_front(nxt, "cancelled")
    if saved.get("provider") != provider or not state or state != saved.get("state") or not code:
        return _back_to_front(nxt, "state_mismatch")
    try:
        p = _provider(provider)
    except ApiError:
        return _back_to_front(nxt, "provider_not_available")

    def work() -> User:
        token = p.exchange(code, saved.get("verifier"))
        user = oauth.find_or_create_user(db, p.profile(token))
        db.commit()
        return user

    try:
        user = await run_in_threadpool(work)
    except oauth.OAuthError as e:
        db.rollback()
        log.warning("소셜 로그인 실패 provider=%s reason=%s", provider, e)
        return _back_to_front(nxt, str(e))
    # login=ok: 프론트가 새로 생긴 리프레시 쿠키로 로그인을 복구하라는 신호
    resp = _back_to_front(nxt + ("&" if "?" in nxt else "?") + "login=ok")
    _issue(resp, user)  # 리프레시 쿠키만 쓴다 (액세스 토큰은 프론트가 /auth/refresh 로 받는다)
    return resp
