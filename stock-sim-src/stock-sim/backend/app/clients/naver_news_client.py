"""네이버 뉴스 검색. 키: developers.naver.com (검색 API 등록)

TODO(verify): display 1~100, start 1~1000(start+display-1 <= 1000), pubDate 형식(RFC 822로 알려짐),
일일 호출 한도는 공식 문서로 확인하지 못했다(서드파티 자료만). 본문 크롤링 금지 — 제목/요약만 쓴다.
"""

from __future__ import annotations

import os

from app.clients.http import RateLimiter, UpstreamError, request_json

URL = "https://openapi.naver.com/v1/search/news.json"
MAX_START = 1000
_limiter = RateLimiter(0.12)  # 초당 10회 이하로  # TODO(verify)


def search_news(query: str, display: int = 100, start: int = 1, sort: str = "date") -> list[dict]:
    cid, secret = os.getenv("NAVER_CLIENT_ID", ""), os.getenv("NAVER_CLIENT_SECRET", "")
    if not cid or not secret:
        raise UpstreamError("NAVER_CLIENT_ID/SECRET 이 설정되지 않았습니다")
    if start > MAX_START:
        return []
    display = min(display, MAX_START - start + 1, 100)
    data = request_json(
        "GET",
        URL,
        headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret},
        params={"query": query, "display": display, "start": start, "sort": sort},
        limiter=_limiter,
        name="Naver",
    )
    return data.get("items", [])  # title, originallink, link, description, pubDate
