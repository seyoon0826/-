"""외부 API 공통 HTTP (SPEC 5).

타임아웃 15초, 재시도 최대 3회(지수 백오프 1/2/4초), 4xx(429 제외)는 재시도하지 않는다.
클라이언트별 레이트 리미터. 키/헤더는 로그와 예외 메시지에 남기지 않는다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import requests

TIMEOUT = 15
BACKOFF = (1, 2, 4)


class UpstreamError(RuntimeError):
    """외부 API 오류. 메시지에는 URL 쿼리(키 포함 가능)나 헤더를 넣지 않는다."""


class RateLimiter:
    def __init__(self, min_interval: float, clock=time.monotonic, sleep=time.sleep) -> None:
        self.min_interval, self._clock, self._sleep = min_interval, clock, sleep
        self._last = -1e18
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            gap = self.min_interval - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
            self._last = self._clock()


def request(
    method: str,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    name: str = "외부 API",
    **kw,
) -> requests.Response:
    last = "알 수 없는 오류"
    for attempt in range(len(BACKOFF) + 1):
        if limiter:
            limiter.wait()
        try:
            r = requests.request(method, url, timeout=TIMEOUT, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
            elif 400 <= r.status_code < 500:
                raise UpstreamError(f"{name} 요청 거부: HTTP {r.status_code}")
            else:
                return r
        except requests.RequestException as e:
            last = e.__class__.__name__
        if attempt < len(BACKOFF):
            sleep(BACKOFF[attempt])
    raise UpstreamError(f"{name} 요청 실패(재시도 소진): {last}")


def request_json(method: str, url: str, **kw):
    r = request(method, url, **kw)
    try:
        return r.json()
    except ValueError as e:
        raise UpstreamError(f"{kw.get('name', '외부 API')} 응답이 JSON 이 아닙니다") from e
