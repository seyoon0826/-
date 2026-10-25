"""저장된 모델(LightGBM up/out 분류기 + q10/q50/q90) 로드와 추론 (SPEC 8.3, 8.4).

기동 시 features.json 과 서빙 피처 목록(FEATURES)이 다르면 FeatureMismatch 로 기동을 거부한다 (MUST).
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from src.ensemble import SeedEnsemble  # app.ml 패키지 초기화가 ml/ 을 sys.path 에 넣는다

from app.ml.features import FEATURES, build_features


class ModelNotFound(RuntimeError):
    pass


class FeatureMismatch(RuntimeError):
    pass


@dataclass
class ModelBundle:
    version: str
    up_clf: SeedEnsemble
    out_clf: SeedEnsemble
    quantiles: dict[str, lgb.Booster]
    calibrator_up: Any | None
    calibrator_out: Any | None
    features: list[str]
    meta: dict[str, Any]

    @classmethod
    def load(cls, model_dir: str | Path, version: str) -> ModelBundle:
        d = Path(model_dir) / version
        if not (d / "features.json").exists():
            raise ModelNotFound(f"모델 산출물이 없습니다: {d}")
        features = json.loads((d / "features.json").read_text())
        if features != FEATURES:
            missing, extra = sorted(set(FEATURES) - set(features)), sorted(set(features) - set(FEATURES))
            raise FeatureMismatch(
                f"모델 {version} 의 features.json 이 서빙 피처와 다릅니다 "
                f"(서빙에만 있음: {missing}, 모델에만 있음: {extra}, 순서 차이 포함). 모델을 다시 학습하세요."
            )

        def _cal(name: str) -> Any | None:
            if not (d / name).exists():
                return None
            with open(d / name, "rb") as fh:
                return pickle.load(fh)  # noqa: S301 - 자체 학습 산출물만 로드한다

        return cls(
            version=version,
            up_clf=SeedEnsemble.load(d, "up_clf"),
            out_clf=SeedEnsemble.load(d, "out_clf"),
            quantiles={k: lgb.Booster(model_file=str(d / f"{k}.txt")) for k in ("q10", "q50", "q90")},
            calibrator_up=_cal("calibrator_up.pkl"),
            calibrator_out=_cal("calibrator_out.pkl"),
            features=features,
            meta=json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {},
        )

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        """X: FEATURES 컬럼. 반환: p_up, p_outperform, q10, q50, q90.
        분위수는 정렬해서 q10 <= q50 <= q90 을 보장한다 (FR-PR-3)."""
        Xf = X[self.features]
        p_up = self.up_clf.predict(Xf)
        if self.calibrator_up is not None:
            p_up = self.calibrator_up.predict(p_up)
        p_out = self.out_clf.predict(Xf)
        if self.calibrator_out is not None:
            p_out = self.calibrator_out.predict(p_out)
        q = np.column_stack([self.quantiles[k].predict(Xf) for k in ("q10", "q50", "q90")])
        q.sort(axis=1)
        return pd.DataFrame(
            {
                "p_up": np.clip(p_up, 0, 1),
                "p_outperform": np.clip(p_out, 0, 1),
                "q10": q[:, 0],
                "q50": q[:, 1],
                "q90": q[:, 2],
            },
            index=X.index,
        )


def serving_frame(
    prices: pd.DataFrame, index: pd.DataFrame | None, macro: pd.DataFrame | None, as_of: date
) -> pd.DataFrame:
    """기준일 as_of 의 종목별 피처 행 (as_of 종가까지의 데이터만 사용). 학습과 같은 build_features 를 쓴다."""
    ts = pd.Timestamp(as_of)
    p = prices[pd.to_datetime(prices["date"]) <= ts]
    i = index[pd.to_datetime(index["date"]) <= ts] if index is not None else None
    m = macro[pd.to_datetime(macro["date"]) <= ts] if macro is not None else None
    f = build_features(p, i, m)
    return f[f["date"] == ts].reset_index(drop=True)


def load_inputs(
    session: Any, end: date, lookback_days: int = 420
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """DB 에서 서빙에 필요한 기간(252거래일 피처 + 여유)의 가격/지수/거시 데이터를 읽는다."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.models import IndexDaily, MacroDaily, PriceDaily, Stock

    start = end - timedelta(days=lookback_days)
    codes = list(session.scalars(select(Stock.code).where(Stock.active.is_(True))))
    names = [
        "code",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "value",
        "foreign_net",
        "inst_net",
        "market_cap",
        "per",
        "pbr",
        "div_yield",
    ]
    cols = [getattr(PriceDaily, n) for n in names]
    prices = pd.DataFrame(
        session.execute(
            select(*cols).where(PriceDaily.code.in_(codes), PriceDaily.date.between(start, end))
        ).all(),
        columns=[c.key for c in cols],
    )
    index = pd.DataFrame(
        session.execute(
            select(IndexDaily.date, IndexDaily.close).where(
                IndexDaily.index_code == "KOSPI", IndexDaily.date.between(start, end)
            )
        ).all(),
        columns=["date", "close"],
    )
    macro = pd.DataFrame(
        session.execute(
            select(MacroDaily.series, MacroDaily.date, MacroDaily.value).where(MacroDaily.date <= end)
        ).all(),
        columns=["series", "date", "value"],
    )
    return prices, index, macro
