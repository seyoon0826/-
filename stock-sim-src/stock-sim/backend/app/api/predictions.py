"""예측 API (SPEC 10.2). 비로그인 허용 (FR-AU-3). 응답 meta 에 면책 문구 (SPEC 1.4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import DISCLAIMER, ApiError, envelope
from app.core.config import settings
from app.core.db import get_db
from app.models import (
    News,
    Prediction,
    PredictionFactor,
    PredictionNews,
    PredictionOutcome,
    PredictionScenario,
    Stock,
)
from app.services.market_data import KST
from app.services.prediction_service import latest_prediction, price_range

router = APIRouter()
META = {"disclaimer": DISCLAIMER}


def _range(p: Prediction) -> dict[str, Any]:
    q10, q50, q90 = float(p.q10), float(p.q50), float(p.q90)
    return {"q10": q10, "q50": q50, "q90": q90, **price_range(p.close_at_asof, q10, q50, q90)}


def _outcome(o: PredictionOutcome | None) -> dict[str, Any] | None:
    if o is None:
        return None
    return {
        "target_date": o.target_date.isoformat(),
        "actual_return": float(o.actual_return),
        "actual_excess": float(o.actual_excess) if o.actual_excess is not None else None,
        "hit_direction": o.hit_direction,
        "in_range": o.in_range,
    }


def _stock_or_404(db: Session, code: str) -> Stock:
    s = db.get(Stock, code)
    if s is None:
        raise ApiError(404, "UNKNOWN_SYMBOL", f"알 수 없는 종목입니다: {code}")
    return s


@router.get("/{code}/prediction")
async def get_prediction(code: str, db: Session = Depends(get_db)):
    def work() -> dict[str, Any]:
        _stock_or_404(db, code)
        p = latest_prediction(db, code, settings.model_version or None)
        if p is None:
            raise ApiError(404, "PREDICTION_NOT_FOUND", "아직 예측이 없습니다. 다음 장 마감 후 생성됩니다.")
        factors = db.scalars(
            select(PredictionFactor)
            .where(PredictionFactor.prediction_id == p.id)
            .order_by(PredictionFactor.rank)
        ).all()
        news = db.scalars(
            select(News)
            .join(PredictionNews, PredictionNews.news_id == News.id)
            .where(PredictionNews.prediction_id == p.id)
            .order_by(News.published_at.desc())
        ).all()
        sc = db.get(PredictionScenario, p.id)
        return {
            "code": p.code,
            "as_of_date": p.as_of_date.isoformat(),
            "horizon": p.horizon,
            "close": p.close_at_asof,
            "p_up": float(p.p_up),
            "p_outperform": float(p.p_outperform) if p.p_outperform is not None else None,
            "range": _range(p),
            "factors": [
                {
                    "rank": f.rank,
                    "feature": f.feature,
                    "label": f.label,
                    "value": float(f.value) if f.value is not None else None,
                    "shap": float(f.shap_value),
                    "direction": "+" if f.shap_value >= 0 else "-",
                }
                for f in factors
            ],
            "news": [
                {"title": n.title, "url": n.url, "published_at": n.published_at.astimezone(KST).isoformat()}
                for n in news
            ],
            "scenarios": (
                {
                    "summary": sc.summary,
                    "bull": sc.bull,
                    "base": sc.base,
                    "bear": sc.bear,
                    "generated_by": sc.generated_by,
                }
                if sc
                else None
            ),
            "model_version": p.model_version,
        }

    return envelope(await run_in_threadpool(work), META)


@router.get("/{code}/prediction/history")
async def prediction_history(code: str, limit: int = Query(30, ge=1, le=250), db: Session = Depends(get_db)):
    """최근 예측과 (채워졌으면) 실제 결과. 이 종목의 적중 기록 화면용 (SPEC 13.2-7)."""

    def work() -> list[dict[str, Any]]:
        _stock_or_404(db, code)
        q = (
            select(Prediction, PredictionOutcome)
            .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
            .where(Prediction.code == code)
        )
        if settings.model_version:
            q = q.where(Prediction.model_version == settings.model_version)
        rows = db.execute(q.order_by(Prediction.as_of_date.desc()).limit(limit)).all()
        return [
            {
                "as_of_date": p.as_of_date.isoformat(),
                "close": p.close_at_asof,
                "p_up": float(p.p_up),
                "range": _range(p),
                "model_version": p.model_version,
                "outcome": _outcome(o),
            }
            for p, o in rows
        ]

    items = await run_in_threadpool(work)
    done = [i for i in items if i["outcome"]]
    summary = {
        "n": len(done),
        "hit_rate": sum(i["outcome"]["hit_direction"] for i in done) / len(done) if done else None,
        "range_coverage": sum(i["outcome"]["in_range"] for i in done) / len(done) if done else None,
    }
    return envelope(items, {**META, "summary": summary})
