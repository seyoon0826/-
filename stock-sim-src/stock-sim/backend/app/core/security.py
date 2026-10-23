"""비밀번호 해시와 JWT (SPEC 6.8 FR-AU-1/2).

- 비밀번호: argon2id (argon2-cffi 기본 파라미터)
- 액세스 토큰: 30분, Authorization: Bearer 로 보낸다
- 리프레시 토큰: 14일, httpOnly 쿠키. 서버에 저장하지 않고 users.token_version 을 담아
  로그아웃(버전 +1) 때 그 사용자의 리프레시 토큰을 한꺼번에 무효로 만든다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings

_ph = PasswordHasher()


class InvalidToken(Exception):
    pass


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _ph.check_needs_rehash(password_hash)


def _secret() -> str:
    if not settings.jwt_secret:
        raise InvalidToken("JWT_SECRET 이 설정되지 않았습니다")
    return settings.jwt_secret


def create_access_token(user_id: int, now: datetime | None = None, minutes: int | None = None) -> str:
    now = now or datetime.now(UTC)
    exp = now + timedelta(minutes=minutes or settings.access_token_minutes)
    payload = {"sub": str(user_id), "type": "access", "iat": now, "exp": exp}
    return jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int, version: int, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    exp = now + timedelta(days=settings.refresh_token_days)
    payload = {"sub": str(user_id), "type": "refresh", "ver": version, "iat": now, "exp": exp}
    return jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)


def _decode(token: str, kind: str) -> dict:
    try:
        payload = jwt.decode(token, _secret(), algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as e:
        raise InvalidToken(e.__class__.__name__) from e
    if payload.get("type") != kind:
        raise InvalidToken(f"{kind} 토큰이 아닙니다")
    return payload


def decode_access_token(token: str) -> int:
    return int(_decode(token, "access")["sub"])


def decode_refresh_token(token: str) -> tuple[int, int]:
    """(user_id, token_version)"""
    p = _decode(token, "refresh")
    return int(p["sub"]), int(p.get("ver", -1))
