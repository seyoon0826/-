"""JWT 액세스 토큰 (SPEC 6.8 FR-AU-2). 발급 API(가입/로그인)는 Phase 8 에서 만든다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import settings


class InvalidToken(Exception):
    pass


def _secret() -> str:
    if not settings.jwt_secret:
        raise InvalidToken("JWT_SECRET 이 설정되지 않았습니다")
    return settings.jwt_secret


def create_access_token(user_id: int, now: datetime | None = None, minutes: int | None = None) -> str:
    now = now or datetime.now(UTC)
    exp = now + timedelta(minutes=minutes or settings.access_token_minutes)
    payload = {"sub": str(user_id), "type": "access", "iat": now, "exp": exp}
    return jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int:
    try:
        payload = jwt.decode(token, _secret(), algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as e:
        raise InvalidToken(e.__class__.__name__) from e
    if payload.get("type") != "access":
        raise InvalidToken("access 토큰이 아닙니다")
    return int(payload["sub"])
