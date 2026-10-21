"""API 공통: 응답 envelope, 에러 형식, Redis 시세 저장소 의존성 (SPEC 10장 공통)."""

from functools import lru_cache

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.services.market_store import QuoteStore


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code, self.code, self.message = status_code, code, message


def envelope(data, meta: dict | None = None) -> dict:
    return {"data": data, "meta": meta or {}}


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}}
    )


@lru_cache
def _default_store() -> QuoteStore:
    return QuoteStore.from_url(settings.redis_url)


def get_store() -> QuoteStore:
    """테스트에서는 app.dependency_overrides[get_store] 로 교체한다."""
    return _default_store()
