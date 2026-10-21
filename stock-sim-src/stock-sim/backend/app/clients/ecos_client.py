"""한국은행 ECOS. 키: ecos.bok.or.kr

URL 형식: /StatisticSearch/{키}/json/kr/{시작건}/{끝건}/{통계표}/{주기}/{시작}/{끝}/{항목}
TODO(verify): 통계표/항목 코드(722Y001/0101000 기준금리, 731Y001/0000001 원/달러), 주기, 결과 없음 코드
(INFO-200). 서드파티 자료로만 확인했고 실제 키로는 호출해 보지 못했다. 요청 URL 에 키가 들어가므로
예외 메시지에 URL 을 남기지 않는다(http.request 가 보장).
"""

from __future__ import annotations

import os

from app.clients.http import RateLimiter, UpstreamError, request_json

BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
PAGE = 1000
_limiter = RateLimiter(0.2)

BASE_RATE = ("722Y001", "D", "0101000")  # 기준금리
USDKRW = ("731Y001", "D", "0000001")  # 원/달러 환율


def series(stat_code: str, cycle: str, start: str, end: str, item_code: str) -> list[dict]:
    """cycle: D(일) M(월) Q(분기) A(연). start/end 는 cycle 에 맞춤(D=YYYYMMDD). 페이지(1,000건) 순회."""
    key = os.getenv("ECOS_API_KEY", "")
    if not key:
        raise UpstreamError("ECOS_API_KEY 가 설정되지 않았습니다")
    rows: list[dict] = []
    first = 1
    while True:
        url = f"{BASE}/{key}/json/kr/{first}/{first + PAGE - 1}/{stat_code}/{cycle}/{start}/{end}/{item_code}"
        data = request_json("GET", url, limiter=_limiter, name="ECOS")
        if "StatisticSearch" not in data:
            result = data.get("RESULT", {})
            if str(result.get("CODE", "")).startswith("INFO-200"):  # 해당 데이터 없음
                break
            raise UpstreamError(f"ECOS 오류: {result.get('CODE')} {result.get('MESSAGE')}")
        block = data["StatisticSearch"]
        rows.extend(block.get("row", []))
        total = int(block.get("list_total_count", len(rows)))
        if len(rows) >= total:
            break
        first += PAGE
    return rows  # TIME, DATA_VALUE 등


def base_rate(start: str, end: str) -> list[dict]:
    return series(*BASE_RATE[:2], start, end, BASE_RATE[2])


def usdkrw(start: str, end: str) -> list[dict]:
    return series(*USDKRW[:2], start, end, USDKRW[2])
