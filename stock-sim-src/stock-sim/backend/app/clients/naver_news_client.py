"""네이버 뉴스 검색. 키: developers.naver.com (검색 API 등록)"""
import os
import requests

URL = "https://openapi.naver.com/v1/search/news.json"


def search_news(query: str, display: int = 100, start: int = 1, sort: str = "date") -> list[dict]:
    headers = {
        "X-Naver-Client-Id": os.getenv("NAVER_CLIENT_ID", ""),
        "X-Naver-Client-Secret": os.getenv("NAVER_CLIENT_SECRET", ""),
    }
    r = requests.get(URL, headers=headers, timeout=15,
                     params={"query": query, "display": display, "start": start, "sort": sort})
    r.raise_for_status()
    return r.json().get("items", [])  # title, originallink, link, description, pubDate
