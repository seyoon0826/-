"""네이버 뉴스 검색 -> news / news_stock_map (FR-CL-1/3/4/6). 제목+요약만 사용, 본문 크롤링 금지.

한계: 검색 API 는 질의당 최대 1,000건(start<=1000)까지만 과거로 간다.
그 이전 뉴스는 별도 확보(Q5, 빅카인즈)가 필요하다.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.clients import naver_news_client as nv
from app.core.db import SessionLocal
from app.models import News, NewsStockMap, Stock
from app.pipelines.common import JobResult, SessionFactory, job_run
from app.services.market_data import KST

log = logging.getLogger(__name__)

PAGE = 100
REL_TITLE, REL_SUMMARY, REL_QUERY_ONLY = Decimal("1.00"), Decimal("0.60"), Decimal("0.30")  # FR-CL-4
_TAG = re.compile(r"<[^>]+>")
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|ref$|cid$)", re.I)


def strip_html(s: str) -> str:
    return html.unescape(_TAG.sub("", s or "")).strip()


def normalize_url(url: str) -> str:
    """중복 제거용 정규화: 스킴/호스트 소문자, 프래그먼트와 추적 파라미터 제거, 쿼리 정렬, 끝 슬래시 제거."""
    p = urlsplit(url.strip())
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k))
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(query), ""))


def parse_pubdate(s: str) -> datetime:
    """RFC 822 pubDate -> KST 타임존 포함 datetime (FR-CL-3).  # TODO(verify): 형식은 서드파티 자료 기준"""
    return parsedate_to_datetime(s).astimezone(KST)


def relevance(terms: list[str], title: str, summary: str) -> Decimal | None:
    """제목에 있으면 1.0, 요약에만 있으면 0.6, 없으면 None (검색에만 걸린 경우는 호출 쪽에서 0.3)."""
    t, sm = title.lower(), summary.lower()
    if any(x.lower() in t for x in terms):
        return REL_TITLE
    if any(x.lower() in sm for x in terms):
        return REL_SUMMARY
    return None


@dataclass(frozen=True)
class Article:
    url: str
    title: str
    summary: str
    source: str | None
    published_at: datetime


def parse_item(item: dict) -> Article | None:
    raw_url = item.get("originallink") or item.get("link")
    if not raw_url or not item.get("pubDate"):
        return None
    try:
        published = parse_pubdate(item["pubDate"])
    except (TypeError, ValueError):
        return None
    title = strip_html(item.get("title", ""))
    if not title:
        return None
    url = normalize_url(raw_url)
    return Article(
        url, title, strip_html(item.get("description", "")), urlsplit(url).netloc or None, published
    )


def _save_page(session, articles: list[Article], stocks: list[tuple[str, list[str]]], searched: str) -> int:
    """기사 upsert + 종목 매핑. 새로 들어간 기사 수를 반환한다."""
    unique = {a.url: a for a in articles}
    stmt = insert(News).values(
        [
            {
                "url": a.url,
                "title": a.title,
                "summary": a.summary,
                "source": a.source,
                "published_at": a.published_at,
            }
            for a in unique.values()
        ]
    )
    inserted = session.execute(stmt.on_conflict_do_nothing(index_elements=["url"]).returning(News.url)).all()
    ids = dict(session.execute(select(News.url, News.id).where(News.url.in_(list(unique)))).all())

    maps: dict[tuple[int, str], Decimal] = {}
    for a in unique.values():
        for code, terms in stocks:
            rel = relevance(terms, a.title, a.summary)
            if rel is None and code == searched:
                rel = REL_QUERY_ONLY  # 검색 결과에만 걸린 경우
            if rel is not None:
                key = (ids[a.url], code)
                maps[key] = max(rel, maps.get(key, Decimal(0)))
    if maps:
        m = insert(NewsStockMap).values(
            [{"news_id": nid, "code": code, "relevance": rel} for (nid, code), rel in maps.items()]
        )
        session.execute(
            m.on_conflict_do_update(
                index_elements=["news_id", "code"],
                set_={"relevance": m.excluded.relevance},
                where=NewsStockMap.relevance < m.excluded.relevance,  # 관련도는 더 높아질 때만 갱신
            )
        )
    return len(inserted)


def run(
    *, search=nv.search_news, session_factory: SessionFactory = SessionLocal, max_pages: int = 10
) -> JobResult:
    """활성 종목별로 최신 기사부터 페이지를 내려가며 수집한다. 한 페이지가 전부 기존 기사면 멈춘다(증분)."""
    with job_run("collect_news", session_factory) as res:
        with session_factory() as s:
            rows = s.execute(
                select(Stock.code, Stock.name, Stock.aliases).where(Stock.active.is_(True))
            ).all()
        stocks = [(c, [n, *(a or [])]) for c, n, a in rows]
        for code, terms in stocks:
            try:
                total = 0
                for query in dict.fromkeys(terms):  # 종목명 + 별칭
                    for page in range(max_pages):
                        items = search(query, display=PAGE, start=page * PAGE + 1, sort="date")
                        articles = [a for a in map(parse_item, items) if a]
                        if not items:
                            break
                        new = 0
                        if articles:
                            with session_factory() as s:
                                new = _save_page(s, articles, stocks, code)
                                s.commit()
                        total += new
                        if new == 0 or len(items) < PAGE:
                            break
                log.info("%s: 신규 기사 %d건", code, total)
                res.ok += 1
            except Exception as e:
                log.warning("%s 뉴스 수집 실패: %s", code, e.__class__.__name__)
                res.failed.append(f"{code}: {e.__class__.__name__}")
    return res
