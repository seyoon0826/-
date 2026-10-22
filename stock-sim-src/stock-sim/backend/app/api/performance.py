"""모델 성적표 (SPEC 10.2, 6.5 FR-PF). GET /model/performance?window=60&scope=live

모델과 베이스라인 3종(항상 상승, 5일 모멘텀, KOSPI 추종)의 지표를 같은 표에 둔다. 베이스라인보다 나쁜 지표도
그대로 보여준다 (FR-PF-6). 오프라인(walk-forward)과 라이브 결과는 scope 로 구분한다 (FR-PF-5).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import DISCLAIMER, ApiError, envelope
from app.core.config import settings
from app.core.db import get_db
from app.models import ModelMetric
from app.services.prediction_service import calibration_live

router = APIRouter()

LOWER_IS_BETTER = {"brier", "pinball_q10", "pinball_q50", "pinball_q90"}


@router.get("/performance")
async def performance(
    window: Literal["20", "60", "250", "all"] = "60",
    scope: Literal["live", "offline"] = "live",
    db: Session = Depends(get_db),
):
    version = settings.model_version
    if not version:
        raise ApiError(404, "PREDICTION_NOT_FOUND", "설정된 예측 모델이 없습니다.")
    eff_window = "all" if scope == "offline" else window  # 오프라인 결과는 전체 구간 하나뿐

    def work() -> dict[str, Any]:
        rows = db.scalars(
            select(ModelMetric).where(
                ModelMetric.model_version == version,
                ModelMetric.scope == scope,
                ModelMetric.window == eff_window,
            )
        ).all()
        strategies: dict[str, dict[str, Any]] = {}
        for r in rows:
            s = strategies.setdefault(r.strategy, {"n": r.n})
            s[r.metric] = float(r.value) if r.value is not None else None
        if scope == "offline":
            path = Path(settings.model_dir) / version / "metrics.json"
            calibration = json.loads(path.read_text()).get("calibration_up", []) if path.exists() else []
        else:
            calibration = calibration_live(db, version, eff_window)
        best = {}
        model = strategies.get("model", {})
        for metric in ("direction_accuracy", "brier", "pinball_q50", "range_coverage", "ic"):
            vals = {k: v.get(metric) for k, v in strategies.items() if v.get(metric) is not None}
            if vals:
                pick = min if metric in LOWER_IS_BETTER else max
                if metric == "range_coverage":  # 목표 80% 에 가까울수록 좋다
                    best[metric] = min(vals, key=lambda k: abs(vals[k] - 0.8))
                else:
                    best[metric] = pick(vals, key=vals.get)
        return {
            "model_version": version,
            "scope": scope,
            "window": eff_window,
            "strategies": strategies,
            "best": best,  # 지표별로 가장 좋은 전략 (화면 강조용, 모델이 아니어도 그대로 표시)
            "calibration_up": calibration,
            "range_coverage_target": 0.8,
            "n": model.get("n", 0),
        }

    data = await run_in_threadpool(work)
    note = (
        None if data["n"] else "아직 결과가 채워진 라이브 예측이 없습니다 (예측 후 5거래일이 지나야 채워짐)."
    )
    return envelope(data, {"disclaimer": DISCLAIMER, "note": note})
