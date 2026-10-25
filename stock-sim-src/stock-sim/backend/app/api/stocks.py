"""시세/차트 (SPEC 10.1). GET /stocks, /{code}/quote, /{code}/orderbook, /{code}/chart?range="""

from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import ApiError, envelope, get_store
from app.core.config import settings
from app.core.db import get_db
from app.models import Disclosure, News, NewsStockMap, PriceDaily, Stock
from app.services.market_data import KST, make_quote
from app.services.market_store import QuoteStore
from app.services.prediction_service import expected_from_rank, latest_prediction, ranks_on
from app.services.providers._book import synth_orderbook

router = APIRouter()

RANGE_DAYS = {"1M": 31, "3M": 93, "6M": 186, "1Y": 366, "3Y": 1096}
MAX_MARKERS_PER_DAY = 3


def _stock_or_404(db: Session, code: str) -> Stock:
    stock = db.get(Stock, code)
    if stock is None:
        raise ApiError(404, "UNKNOWN_SYMBOL", f"알 수 없는 종목입니다: {code}")
    return stock


def _last_two_bars(db: Session, code: str):
    return db.execute(
        select(PriceDaily.date, PriceDaily.close, PriceDaily.volume)
        .where(PriceDaily.code == code)
        .order_by(PriceDaily.date.desc())
        .limit(2)
    ).all()


def _db_quote(db: Session, code: str):
    """Redis 에 시세가 없을 때의 대체: 최근 종가 (source=pykrx)."""
    rows = _last_two_bars(db, code)
    if not rows:
        return None
    prev = rows[1].close if len(rows) > 1 else None
    ts = datetime.combine(rows[0].date, dtime(15, 30), tzinfo=KST)
    return make_quote(code, rows[0].close, prev, rows[0].volume, ts, "pykrx")


def _quote_dict(q) -> dict:
    return {
        "price": q.price,
        "change": q.change,
        "change_pct": q.change_pct,
        "volume": q.volume,
        "ts": q.ts.astimezone(KST).isoformat(),
        "source": q.source,
    }


@router.get("")
async def list_stocks(db: Session = Depends(get_db), store: QuoteStore = Depends(get_store)):
    def load():
        stocks = list(db.scalars(select(Stock).where(Stock.active.is_(True)).order_by(Stock.code)))
        version = settings.model_version or None
        latest_pred = {}
        for st in stocks:  # 종목별 최신 기준일 예측 (서빙 중인 모델, 30종목이라 단순 조회로 충분)
            pred = latest_prediction(db, st.code, version)
            if pred:
                latest_pred[st.code] = pred
        ranks = {d: ranks_on(db, d, version) for d in {p.as_of_date for p in latest_pred.values()}}
        return stocks, latest_pred, ranks, {s.code: _db_quote(db, s.code) for s in stocks}

    stocks, preds, ranks, fallback = await run_in_threadpool(load)
    out = []
    for s in stocks:
        q = await store.get_quote(s.code) or fallback[s.code]
        p = preds.get(s.code)
        out.append(
            {
                "code": s.code,
                "name": s.name,
                "price": q.price if q else None,
                "change_pct": q.change_pct if q else None,
                "p_up": float(p.p_up) if p else None,
                "as_of_date": p.as_of_date.isoformat() if p else None,
                # 같은 날 종목 중 상승확률 순위 (1 = 가장 높음)
                "rank": ranks[p.as_of_date].get(s.code, (None, None))[0] if p else None,
                "rank_of": ranks[p.as_of_date].get(s.code, (None, None))[1] if p else None,
                # 5거래일 뒤 수익률 예상: 중앙값과 10~90% 범위 (소수, 0.012 = +1.2%)
                "q10": float(p.q10) if p else None,
                "q50": float(p.q50) if p else None,
                "q90": float(p.q90) if p else None,
                # 같은 순위대 종목의 과거 실제 5일 평균 수익률 (walk-forward 테스트 구간)
                "expected": (
                    expected_from_rank(
                        settings.model_dir, p.model_version, *ranks[p.as_of_date].get(s.code, (None, None))
                    )
                    if p
                    else None
                ),
            }
        )
    return envelope(out)


@router.get("/{code}/quote")
async def get_quote(code: str, db: Session = Depends(get_db), store: QuoteStore = Depends(get_store)):
    await run_in_threadpool(_stock_or_404, db, code)
    q = await store.get_quote(code) or await run_in_threadpool(_db_quote, db, code)
    if q is None:
        raise ApiError(503, "UPSTREAM_UNAVAILABLE", "시세 데이터가 아직 없습니다.")
    return envelope(_quote_dict(q))


@router.get("/{code}/orderbook")
async def get_orderbook(code: str, db: Session = Depends(get_db), store: QuoteStore = Depends(get_store)):
    await run_in_threadpool(_stock_or_404, db, code)
    ob = await store.get_orderbook(code)
    if ob is None:  # 실시간 호가가 없으면 종가 기준 합성 호가
        q = await run_in_threadpool(_db_quote, db, code)
        if q is None:
            raise ApiError(503, "UPSTREAM_UNAVAILABLE", "호가 데이터가 아직 없습니다.")
        ob = synth_orderbook(code, q.price, 10_000, q.ts, "pykrx")
    return envelope(
        {
            "asks": [{"price": lv.price, "qty": lv.qty} for lv in ob.asks],
            "bids": [{"price": lv.price, "qty": lv.qty} for lv in ob.bids],
            "ts": ob.ts.astimezone(KST).isoformat(),
            "source": ob.source,
        }
    )


def _markers(db: Session, code: str, start: date) -> list[dict]:
    """FR-ST-4: 해당 날짜의 뉴스·공시, 하루 최대 3건, 관련도 순."""
    start_dt = datetime.combine(start, dtime.min, tzinfo=KST)
    items: list[tuple[date, float, dict]] = []
    news = db.execute(
        select(News.published_at, News.title, News.url, NewsStockMap.relevance)
        .join(NewsStockMap, NewsStockMap.news_id == News.id)
        .where(NewsStockMap.code == code, News.published_at >= start_dt)
    ).all()
    for n in news:
        d = n.published_at.astimezone(KST).date()
        items.append(
            (d, float(n.relevance), {"date": d.isoformat(), "type": "news", "title": n.title, "url": n.url})
        )
    for x in db.execute(
        select(Disclosure.filed_at, Disclosure.title, Disclosure.rcept_no).where(
            Disclosure.code == code, Disclosure.filed_at >= start_dt
        )
    ):
        d = x.filed_at.astimezone(KST).date()
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={x.rcept_no}"
        items.append((d, 1.0, {"date": d.isoformat(), "type": "disclosure", "title": x.title, "url": url}))
    items.sort(key=lambda t: (t[0], -t[1]))
    per_day: dict[date, int] = {}
    out = []
    for d, _, m in items:
        if per_day.get(d, 0) < MAX_MARKERS_PER_DAY:
            per_day[d] = per_day.get(d, 0) + 1
            out.append(m)
    return out


@router.get("/{code}/chart")
async def get_chart(
    code: str,
    range: Literal["1M", "3M", "6M", "1Y", "3Y"] = Query("6M"),
    db: Session = Depends(get_db),
):
    def load():
        _stock_or_404(db, code)
        last = db.scalar(select(func.max(PriceDaily.date)).where(PriceDaily.code == code))
        if last is None:
            return [], []
        start = last - timedelta(days=RANGE_DAYS[range])
        rows = db.execute(
            select(PriceDaily)
            .where(PriceDaily.code == code, PriceDaily.date >= start)
            .order_by(PriceDaily.date)
        ).scalars()
        candles = [
            {
                "date": r.date.isoformat(),
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in rows
        ]
        return candles, _markers(db, code, start)

    candles, markers = await run_in_threadpool(load)
    return envelope({"candles": candles, "markers": markers})
