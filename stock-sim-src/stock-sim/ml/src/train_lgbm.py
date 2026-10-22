"""LightGBM 학습 (SPEC 8.3, 8.4): 상승확률/초과확률 분류기 2개 + 5일 수익률 분위수 회귀 3개.

실행 (로컬 또는 Colab):
    python -m src.train_lgbm --data-dir data --out models          # ml/ 에서 실행
산출물: models/{model_version}/ up_clf.txt out_clf.txt q10.txt q50.txt q90.txt calibrator_up.pkl
        features.json feature_labels.json metrics.json meta.json params.json
"""

from __future__ import annotations

import argparse
import json
import pickle
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from src import evaluate as ev
from src.data import data_hash, load_csv
from src.features import FEATURE_LABELS, FEATURES, HORIZON, build_features, build_targets
from src.walk_forward import EMBARGO, TRAIN_DAYS, VALID_DAYS, Fold, make_folds

QUANTILES = {"q10": 0.1, "q50": 0.5, "q90": 0.9}
SEED = 42
PARAMS: dict[str, Any] = {
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbose": -1,
    "seed": SEED,
    "deterministic": True,  # 같은 데이터·시드 -> 같은 결과 (SPEC 14 재현성)
    "force_row_wise": True,
    "num_threads": 1,
}
NUM_ROUNDS, EARLY_STOP = 1_000, 50


def make_dataset(
    prices: pd.DataFrame, index: pd.DataFrame | None, macro: pd.DataFrame | None
) -> pd.DataFrame:
    """피처 + 타깃 (학습 전용). 타깃이 없는 마지막 5거래일은 학습에서 빠진다."""
    return build_features(prices, index, macro).merge(
        build_targets(prices, index), on=["code", "date"], how="left"
    )


@dataclass
class Models:
    up_clf: lgb.Booster
    out_clf: lgb.Booster
    quantiles: dict[str, lgb.Booster]
    calibrator_up: IsotonicRegression | None = None

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        X = X[FEATURES]
        p_up = self.up_clf.predict(X)
        if self.calibrator_up is not None:
            p_up = self.calibrator_up.predict(p_up)
        q = np.column_stack([self.quantiles[k].predict(X) for k in QUANTILES])
        q.sort(axis=1)  # 분위수 단조성 보장 q10 <= q50 <= q90 (FR-PR-3)
        return pd.DataFrame(
            {
                "p_up": p_up,
                "p_outperform": self.out_clf.predict(X),
                "q10": q[:, 0],
                "q50": q[:, 1],
                "q90": q[:, 2],
            },
            index=X.index,
        )


def _train(params: dict[str, Any], train: pd.DataFrame, valid: pd.DataFrame, target: str) -> lgb.Booster:
    tr, va = train.dropna(subset=[target]), valid.dropna(subset=[target])
    dtr = lgb.Dataset(tr[FEATURES], tr[target], free_raw_data=False)
    dva = lgb.Dataset(va[FEATURES], va[target], reference=dtr, free_raw_data=False)
    return lgb.train(
        params, dtr, NUM_ROUNDS, valid_sets=[dva], callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)]
    )


def fit_models(train: pd.DataFrame, valid: pd.DataFrame, params: dict[str, Any] | None = None) -> Models:
    """학습 구간으로 학습하고, 검증 구간으로 조기 종료와 확률 보정(isotonic, SHOULD)을 한다."""
    p = {**PARAMS, **(params or {})}
    up = _train({**p, "objective": "binary"}, train, valid, "y_up")
    out = _train({**p, "objective": "binary"}, train, valid, "y_out")
    qs = {
        k: _train({**p, "objective": "quantile", "alpha": a}, train, valid, "ret_fwd")
        for k, a in QUANTILES.items()
    }
    va = valid.dropna(subset=["y_up"])
    cal = None
    if len(va) >= 100 and va["y_up"].nunique() == 2:
        cal = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(
            up.predict(va[FEATURES]), va["y_up"]
        )
    return Models(up, out, qs, cal)


# ---------------------------------------------------------------- 베이스라인 (SPEC 2, FR-PF-2)
def baselines(train: pd.DataFrame, test: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """비교용 단순 전략. 분위수는 학습 구간 수익률의 무조건부 분위수를 각 전략의 중심값에 더해 만든다.
    - always_up: 항상 상승 (P(up)=1), 범위 = 학습 구간 5일 수익률 분위수
    - momentum : 직전 5일 수익률 방향이 이어진다 (P(up)=1 또는 0), 중심값 = 직전 5일 수익률
    - kospi    : KOSPI 직전 5일 방향을 따른다 (P(up)=1 또는 0), 중심값 = KOSPI 직전 5일 수익률
    """
    r = train["ret_fwd"].dropna()
    u10, u50, u90 = (float(r.quantile(a)) for a in (0.1, 0.5, 0.9))  # 학습 구간 무조건부 분위수

    def frame(p_up: pd.Series, center: pd.Series) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": test["date"],
                "p_up": p_up,
                "q10": center + (u10 - u50),
                "q50": center,
                "q90": center + (u90 - u50),
                "y_up": test["y_up"],
                "ret_fwd": test["ret_fwd"],
            }
        )

    ones = pd.Series(1.0, index=test.index)
    return {
        "always_up": frame(ones, ones * u50),
        "momentum": frame((test["ret_5d"] > 0).astype(float), test["ret_5d"].fillna(0.0)),
        "kospi": frame((test["kospi_ret_5d"] > 0).astype(float), test["kospi_ret_5d"].fillna(0.0)),
    }


def walk_forward_eval(
    data: pd.DataFrame, params: dict[str, Any] | None = None, **fold_kw: int
) -> tuple[dict[str, Any], pd.DataFrame]:
    """walk-forward 로 모델과 베이스라인을 같은 테스트 구간에서 비교한다. (지표 dict, 모델 테스트 예측)"""
    folds = make_folds(data["date"], **fold_kw)
    if not folds:
        raise ValueError("walk-forward 폴드를 만들 데이터가 부족합니다")
    preds: list[pd.DataFrame] = []
    base: dict[str, list[pd.DataFrame]] = {"always_up": [], "momentum": [], "kospi": []}
    for f in folds:
        tr, va, te = (data[f.mask(data["date"], part)] for part in ("train", "valid", "test"))
        models = fit_models(tr, va, params)
        pr = models.predict(te).assign(
            date=te["date"],
            code=te["code"],
            y_up=te["y_up"],
            ret_fwd=te["ret_fwd"],
            y_out=te["y_out"],
            fold=f.index,
        )
        preds.append(pr)
        for k, v in baselines(tr, te).items():
            base[k].append(v)
    allp = pd.concat(preds)
    strategies = {"model": allp, **{k: pd.concat(v) for k, v in base.items()}}
    result = {
        "scope": "offline",
        "horizon": HORIZON,
        "folds": [
            {
                "index": f.index,
                **{
                    p: [str(f.__dict__[p][0].date()), str(f.__dict__[p][1].date())]
                    for p in ("train", "valid", "test")
                },
            }
            for f in folds
        ],
        "strategies": {k: ev.metrics(v) for k, v in strategies.items()},
        "calibration_up": ev.calibration_curve(
            allp.dropna(subset=["y_up"])["p_up"], allp.dropna(subset=["y_up"])["y_up"]
        ),
        "baseline_definitions": baselines.__doc__,
    }
    return result, allp


def fit_final(
    data: pd.DataFrame,
    params: dict[str, Any] | None = None,
    train_days: int = TRAIN_DAYS,
    valid_days: int = VALID_DAYS,
    embargo: int = EMBARGO,
) -> tuple[Models, Fold]:
    """서빙용 최종 모델: 가장 최근 학습 3년 + 검증 3개월(조기 종료/보정). 타깃이 있는 날까지만 쓴다."""
    labeled = data.dropna(subset=["ret_fwd"])
    days = sorted(labeled["date"].unique())
    if len(days) < train_days + embargo + valid_days:
        train_days = max(len(days) - embargo - valid_days, 1)
    va_end = len(days) - 1
    va_start = va_end - valid_days + 1
    tr_end = va_start - embargo - 1
    tr_start = max(tr_end - train_days + 1, 0)
    fold = Fold(
        -1,
        (pd.Timestamp(days[tr_start]), pd.Timestamp(days[tr_end])),
        (pd.Timestamp(days[va_start]), pd.Timestamp(days[va_end])),
        (pd.Timestamp(days[va_end]),) * 2,
    )
    tr, va = data[fold.mask(data["date"], "train")], data[fold.mask(data["date"], "valid")]
    return fit_models(tr, va, params), fold


def model_version(dhash: str, params: dict[str, Any], now: datetime | None = None) -> str:
    import hashlib

    now = now or datetime.now(UTC)
    h = hashlib.sha256((dhash + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:6]
    return f"lgbm-{now:%Y%m%d}-{h}"


def save_artifacts(
    out_dir: str | Path,
    version: str,
    models: Models,
    metrics: dict[str, Any],
    meta: dict[str, Any],
    params: dict[str, Any],
) -> Path:
    d = Path(out_dir) / version
    d.mkdir(parents=True, exist_ok=True)
    models.up_clf.save_model(str(d / "up_clf.txt"))
    models.out_clf.save_model(str(d / "out_clf.txt"))
    for k, b in models.quantiles.items():
        b.save_model(str(d / f"{k}.txt"))
    if models.calibrator_up is not None:
        with open(d / "calibrator_up.pkl", "wb") as fh:
            pickle.dump(models.calibrator_up, fh)
    (d / "features.json").write_text(json.dumps(FEATURES, ensure_ascii=False, indent=1))
    (d / "feature_labels.json").write_text(json.dumps(FEATURE_LABELS, ensure_ascii=False, indent=1))
    (d / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1, default=str))
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str))
    (d / "params.json").write_text(json.dumps(params, indent=1))
    return d


def run(
    prices: pd.DataFrame,
    index: pd.DataFrame | None,
    macro: pd.DataFrame | None,
    out_dir: str | Path,
    params: dict[str, Any] | None = None,
    skip_eval: bool = False,
    now: datetime | None = None,
    **fold_kw: int,
) -> Path:
    """전체 파이프라인: 데이터 -> 피처/타깃 -> walk-forward 평가 -> 최종 학습 -> 산출물 저장."""
    params = {**PARAMS, **(params or {})}
    data = make_dataset(prices, index, macro)
    metrics: dict[str, Any] = {"scope": "offline", "skipped": True}
    if not skip_eval:
        metrics, _ = walk_forward_eval(data, params, **fold_kw)
    final_kw = {k: v for k, v in fold_kw.items() if k in ("train_days", "valid_days", "embargo")}
    models, fold = fit_final(data, params, **final_kw)
    dhash = data_hash(prices, index, macro)
    version = model_version(dhash, params, now)
    meta = {
        "model_version": version,
        "created_at": (now or datetime.now(UTC)).isoformat(),
        "train_period": [str(fold.train[0].date()), str(fold.train[1].date())],
        "valid_period": [str(fold.valid[0].date()), str(fold.valid[1].date())],
        "data_period": [
            str(pd.Timestamp(data["date"].min()).date()),
            str(pd.Timestamp(data["date"].max()).date()),
        ],
        "n_codes": int(data["code"].nunique()),
        "n_rows": int(len(data)),
        "data_hash": dhash,
        "horizon": HORIZON,
        "best_iterations": {
            "up_clf": models.up_clf.best_iteration,
            "out_clf": models.out_clf.best_iteration,
            **{k: b.best_iteration for k, b in models.quantiles.items()},
        },
        "calibrated": models.calibrator_up is not None,
        "versions": {
            "python": platform.python_version(),
            "lightgbm": lgb.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
    }
    return save_artifacts(out_dir, version, models, metrics, meta, params)


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(description="StockSim LightGBM 학습")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="models")
    ap.add_argument("--skip-eval", action="store_true")
    a = ap.parse_args()
    prices, index, macro = load_csv(a.data_dir)
    d = run(prices, index, macro, a.out, skip_eval=a.skip_eval)
    print("저장:", d)


if __name__ == "__main__":  # pragma: no cover
    main()
