"""뉴스 수집 (FR-CL-1/3/4/6). 네이버 API 는 가짜 search 함수로 대체."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import News, NewsStockMap, Stock
from app.pipelines import collect_news
from app.pipelines.collect_news import normalize_url, parse_item, parse_pubdate, relevance, strip_html
from app.services.market_data import KST

PUB = "Tue, 20 Oct 2026 09:12:00 +0900"


def item(title, url, desc="", pub=PUB):
    return {
        "title": title,
        "originallink": url,
        "link": "https://n.news.naver.com/x",
        "description": desc,
        "pubDate": pub,
    }


@pytest.fixture
def stocks(sf):
    with sf() as s:
        s.add_all(
            [Stock(code="005930", name="삼성전자", aliases=["삼성"]), Stock(code="000660", name="SK하이닉스")]
        )
        s.commit()


class Search:
    """질의별 페이지 목록을 돌려주는 가짜 search_news. calls 에 (query, start) 기록."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, query, display=100, start=1, sort="date"):
        self.calls.append((query, start))
        pages = self.pages.get(query, [])
        i = (start - 1) // 100
        return pages[i] if i < len(pages) else []


def _maps(sf):
    with sf() as s:
        rows = s.execute(
            select(News.title, NewsStockMap.code, NewsStockMap.relevance).join(NewsStockMap)
        ).all()
    return {(t, c): r for t, c, r in rows}


def _count(sf, model):
    with sf() as s:
        return s.scalar(select(func.count()).select_from(model))


# ---------- 순수 함수 ----------
def test_normalize_url_dedupes_variants():
    base = "https://News.Example.com/a/1"
    assert normalize_url(base + "/?utm_source=x&b=2&a=1#frag") == normalize_url(
        "https://news.example.com/a/1?a=1&b=2"
    )
    assert normalize_url("http://x.com") == "http://x.com/"
    assert normalize_url("https://x.com/a?id=1") != normalize_url("https://x.com/a?id=2")


def test_strip_html_and_entities():
    assert strip_html("<b>삼성전자</b>, &quot;반도체&quot; &amp; 실적") == '삼성전자, "반도체" & 실적'


def test_parse_pubdate_is_kst_aware_FR_CL_3():
    d = parse_pubdate("Tue, 20 Oct 2026 00:30:00 +0000")
    assert d.utcoffset() == timedelta(hours=9) and (d.hour, d.day) == (9, 20) and d.tzinfo == KST


def test_relevance_levels_FR_CL_4():
    assert relevance(["삼성전자"], "삼성전자 실적", "") == Decimal("1.00")
    assert relevance(["삼성전자"], "반도체 업황", "삼성전자가 언급") == Decimal("0.60")
    assert relevance(["삼성전자"], "반도체", "무관") is None
    assert relevance(["SK하이닉스"], "sk하이닉스 급등", "") == Decimal("1.00")  # 대소문자 무시


def test_parse_item_rejects_incomplete():
    assert parse_item({"title": "t", "link": "https://x.com", "pubDate": "garbage"}) is None
    assert parse_item({"title": "t", "pubDate": PUB}) is None
    assert parse_item({"title": "", "link": "https://x.com", "pubDate": PUB}) is None
    a = parse_item(item("<b>삼성</b>전자", "https://x.com/a?utm_x=1"))
    assert a.title == "삼성전자" and a.url == "https://x.com/a" and a.source == "x.com"


# ---------- 파이프라인 ----------
def test_run_stores_articles_with_relevance_and_kst_time(sf, stocks):
    search = Search(
        {
            "삼성전자": [
                [
                    item("<b>삼성전자</b> 실적 발표", "https://a.com/1"),
                    item("반도체 업황", "https://a.com/2", desc="삼성전자 관련 요약"),
                    item("전혀 다른 기사", "https://a.com/3"),
                    item("SK하이닉스 신고가, 삼성전자 추격", "https://a.com/4"),  # 한 기사가 두 종목에 매핑
                ]
            ]
        }
    )
    res = collect_news.run(search=search, session_factory=sf)
    assert res.failed == [] and res.ok == 2
    maps = _maps(sf)
    assert maps[("삼성전자 실적 발표", "005930")] == Decimal("1.00")
    assert maps[("반도체 업황", "005930")] == Decimal("0.60")
    assert maps[("전혀 다른 기사", "005930")] == Decimal("0.30")  # 검색에만 걸림
    assert maps[("SK하이닉스 신고가, 삼성전자 추격", "000660")] == Decimal("1.00")
    assert maps[("SK하이닉스 신고가, 삼성전자 추격", "005930")] == Decimal("1.00")
    assert ("전혀 다른 기사", "000660") not in maps
    with sf() as s:
        n = s.scalars(select(News).where(News.url == "https://a.com/1")).one()
    assert n.published_at.astimezone(KST).hour == 9 and n.published_at.utcoffset() is not None
    assert n.source == "a.com" and n.summary == ""


def test_duplicate_urls_and_reruns_create_no_duplicates_FR_CL_1(sf, stocks):
    page = [
        item("삼성전자 A", "https://a.com/1?utm_source=x"),
        item("삼성전자 A 중복", "https://a.com/1#top"),
    ]
    search = Search({"삼성전자": [page], "삼성": [page]})  # 별칭 질의도 같은 기사를 돌려준다
    collect_news.run(search=search, session_factory=sf)
    assert _count(sf, News) == 1
    collect_news.run(search=search, session_factory=sf)
    assert _count(sf, News) == 1 and _count(sf, NewsStockMap) == 1


def test_alias_is_queried_too(sf, stocks):
    search = Search({})
    collect_news.run(search=search, session_factory=sf)
    assert {q for q, _ in search.calls} == {"삼성전자", "삼성", "SK하이닉스"}


def test_paginates_until_known_articles_then_stops(sf, stocks):
    page1 = [item(f"삼성전자 기사{i}", f"https://a.com/{i}") for i in range(100)]
    page2 = [item(f"삼성전자 기사{i}", f"https://a.com/{i}") for i in range(100, 150)]  # 100개 미만 = 마지막
    s1 = Search({"삼성전자": [page1, page2]})
    collect_news.run(search=s1, session_factory=sf)
    assert [c for c in s1.calls if c[0] == "삼성전자"] == [("삼성전자", 1), ("삼성전자", 101)]
    assert _count(sf, News) == 150
    s2 = Search({"삼성전자": [page1, page2]})  # 재실행: 첫 페이지가 전부 기존 기사 -> 1페이지에서 중단(증분)
    collect_news.run(search=s2, session_factory=sf)
    assert [c for c in s2.calls if c[0] == "삼성전자"] == [("삼성전자", 1)]


def test_max_pages_limits_depth(sf, stocks):
    full = lambda k: [item(f"삼성전자 {k}-{i}", f"https://a.com/{k}/{i}") for i in range(100)]  # noqa: E731
    s = Search({"삼성전자": [full(0), full(1), full(2)]})
    collect_news.run(search=s, session_factory=sf, max_pages=2)
    assert [c for c in s.calls if c[0] == "삼성전자"] == [("삼성전자", 1), ("삼성전자", 101)]


def test_one_stock_failing_does_not_stop_others_FR_CL_6(sf, stocks):
    def search(query, display=100, start=1, sort="date"):
        if query == "삼성전자":
            raise RuntimeError("429")
        return [item("SK하이닉스 소식", "https://a.com/h")] if query == "SK하이닉스" else []

    res = collect_news.run(search=search, session_factory=sf)
    assert res.failed == ["005930: RuntimeError"] and res.ok == 1
    assert _count(sf, News) == 1


def test_relevance_only_increases_on_rerun(sf, stocks):
    # 처음엔 검색에만 걸려 0.3, 나중에 요약 수정으로 제목에 종목명이 들어오면 1.0 으로 올라간다
    collect_news.run(
        search=Search({"삼성전자": [[item("제목 없음", "https://a.com/1")]]}), session_factory=sf
    )
    assert _maps(sf)[("제목 없음", "005930")] == Decimal("0.30")
    with sf() as s:
        s.execute(News.__table__.update().values(title="삼성전자 제목"))
        s.commit()
    collect_news.run(
        search=Search({"삼성전자": [[item("삼성전자 제목", "https://a.com/1")]]}), session_factory=sf
    )
    assert _maps(sf)[("삼성전자 제목", "005930")] == Decimal("1.00")
