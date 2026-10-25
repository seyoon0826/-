"""인증 의존성 (FR-AU-3/4): 모의투자 API 는 로그인 필수. Bearer 액세스 토큰을 검증한다."""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.common import ApiError
from app.core.db import get_db
from app.core.security import InvalidToken, decode_access_token
from app.models import User

_bearer = HTTPBearer(auto_error=False)


def user_id_from_token(token: str | None, db: Session) -> int:
    if not token:
        raise ApiError(401, "UNAUTHORIZED", "로그인이 필요합니다.")
    try:
        uid = decode_access_token(token)
    except InvalidToken:
        raise ApiError(401, "UNAUTHORIZED", "토큰이 유효하지 않거나 만료됐습니다.") from None
    if db.get(User, uid) is None:
        raise ApiError(401, "UNAUTHORIZED", "사용자를 찾을 수 없습니다.")
    return uid


def current_user_id(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> int:
    return user_id_from_token(creds.credentials if creds else None, db)


def optional_user_id(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> int | None:
    """로그인하지 않아도 되는 화면에서 '나'를 표시하려고 쓴다. 토큰이 없거나 틀리면 None."""
    if creds is None:
        return None
    try:
        return user_id_from_token(creds.credentials, db)
    except ApiError:
        return None
