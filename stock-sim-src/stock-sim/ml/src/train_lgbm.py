"""LightGBM 학습 (SPEC 8.3, 8.4): 상승확률/초과확률 분류기 2개 + 5일 수익률 분위수 회귀 3개.

실행 (로컬 또는 Colab):
    python -m src.train_lgbm --data-dir data --out models          # ml/ 에서 실행
산출물: models/{model_version}/ up_clf_{0..4}.txt out_clf_{0..4}.txt (시드 앙상블) q10.txt q50.txt q90.txt
        calibrator_up.pkl calibrator_out.pkl
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

from src import evaluate as ev
from src.calibration import PlattCalibrator
from src.data import data_hash, load_csv, load_members
from src.ensemble import SeedEnsemble
from src.features import (
    FEATURE_LABELS,
    FEATURES,
    HORIZON,
    build_features,
    build_targets,
)
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
ENSEMBLE_SIZE = 5  # 분류기는 시드 SEED..SEED+4 로 5개 학습해 평균 (src/ensemble.py)


def make_dataset(
    prices: pd.DataFrame,
    index: pd.DataFrame | None,
    macro: pd.DataFrame | None,
    members: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """피처 + 타깃 (학습 전용). 타깃이 없는 마지막 5거래일은 학습에서 빠진다.
    members: 시점별 유니버스 명단 (주면 그날 명단 종목만, 순위·상대 라벨도 명단 안에서)."""
    return build_features(prices, index, macro, members).merge(
        build_targets(prices, index, members=members), on=["code", "date"], how="left"
    )


@dataclass
class Models:
    up_clf: SeedEnsemble
    out_clf: SeedEnsemble
    quantiles: dict[str, lgb.Booster]
    calibrator_up: PlattCalibrator | None = None
    calibrator_out: PlattCalibrator | None = None

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        X = X[FEATURES]
        p_up = self.up_clf.predict(X)
        if self.calibrator_up is not None:
            p_up = self.calibrator_up.predict(p_up)
        p_out = self.out_clf.predict(X)
        if self.calibrator_out is not None:
            p_out = self.calibrator_out.predict(p_out)
        q = np.column_stack([self.quantiles[k].predict(X) for k in QUANTILES])
        q.sort(axis=1)  # 분위수 단조성 보장 q10 <= q50 <= q90 (FR-PR-3)
        return pd.DataFrame(
            {
                "p_up": p_up,
                "p_outperform": p_out,
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


def _train_ensemble(
    params: dict[str, Any], train: pd.DataFrame, valid: pd.DataFrame, target: str
) -> SeedEnsemble:
    base = int(params.get("seed", SEED))
    n = int(params.get("ensemble_size", ENSEMBLE_SIZE))
    p = {k: v for k, v in params.items() if k != "ensemble_size"}
    return SeedEnsemble([_train({**p, "seed": base + i}, train, valid, target) for i in range(n)])


def _platt(model: SeedEnsemble, valid: pd.DataFrame, target: str) -> PlattCalibrator | None:
    va = valid.dropna(subset=[target])
    if len(va) < 100 or va[target].nunique() < 2:
        return None
    return PlattCalibrator().fit(model.predict(va[FEATURES]), va[target].to_numpy())


def fit_models(train: pd.DataFrame, valid: pd.DataFrame, params: dict[str, Any] | None = None) -> Models:
    """학습 구간으로 학습하고, 검증 구간으로 조기 종료와 확률 보정(Platt, SHOULD)을 한다.

    상승확률 모델은 절대 방향(y_up)이 아니라 '같은 날 종목 중앙값보다 오를까'(y_rel)를 배운다.
    5일 방향의 상당 부분은 종목 피처로 예측할 수 없는 시장 전체 움직임이라, 절대 방향으로 학습하면
    조기 종료가 1~4회에서 걸리고 모든 종목이 같은 확률이 됐다(2026-10 진단). 학습한 순위 점수를
    검증 구간에서 실제 상승 여부(y_up)에 맞춰 Platt 보정해 P(up) 로 쓴다.
    """
    p = {**PARAMS, **(params or {})}
    up = _train_ensemble({**p, "objective": "binary"}, train, valid, "y_rel")
    out = _train_ensemble({**p, "objective": "binary"}, train, valid, "y_out")
    q = {k: v for k, v in p.items() if k != "ensemble_size"}
    qs = {
        k: _train({**q, "objective": "quantile", "alpha": a}, train, valid, "ret_fwd")
        for k, a in QUANTILES.items()
    }
    return Models(up, out, qs, _platt(up, valid, "y_up"), _platt(out, valid, "y_out"))


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


RANK_BINS = 10


def rank_returns(pred: pd.DataFrame, bins: int = RANK_BINS) -> list[dict[str, Any]]:
    """같은 날 P(up) 순위 구간별 실제 5일 수익률 (walk-forward 테스트 구간, 학습에 안 쓴 기간).
    구간 0 = 상위(확률 높음). 서빙은 종목의 그날 순위로 구간을 골라 '이 순위대 종목의 과거 평균'을 보여준다.
    분위수 중앙값 모델은 시장 잡음 때문에 거의 상수가 돼(2026-10) 종목별 예상 수익률로 쓸 수 없었다."""
    d = pred.dropna(subset=["ret_fwd"]).copy()
    if d.empty:
        return []
    pct = d.groupby("date")["p_up"].rank(ascending=False, method="first", pct=True)  # (0, 1], 작을수록 상위
    d["bin"] = np.minimum((pct * bins).apply(np.ceil).astype(int) - 1, bins - 1).clip(lower=0)
    d["excess"] = d["ret_fwd"] - d.groupby("date")["ret_fwd"].transform("median")
    out = []
    for b, g in d.groupby("bin"):
        out.append(
            {
                "bin": int(b),
                "mean_return": float(g["ret_fwd"].mean()),
                "median_return": float(g["ret_fwd"].median()),
                "mean_excess": float(g["excess"].mean()),
                "up_ratio": float((g["ret_fwd"] > 0).mean()),
                "n": len(g),
            }
        )
    return out


def _positive_ic_folds(pred: pd.DataFrame) -> int:
    """상승확률 순위의 IC 가 0 보다 큰 구간 수 (구간별로 꾸준한지 보기)."""
    n = 0
    for _, g in pred.groupby("fold"):
        if ev.information_coefficient(g["date"], g["p_up"].to_numpy(), g["ret_fwd"].to_numpy()) > 0:
            n += 1
    return n


def walk_forward_eval(
    data: pd.DataFrame,
    params: dict[str, Any] | None = None,
    eval_data: pd.DataFrame | None = None,
    **fold_kw: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """walk-forward 로 모델과 베이스라인을 같은 테스트 구간에서 비교한다. (지표 dict, 모델 테스트 예측)
    eval_data: 테스트 구간을 이 데이터(서비스 종목, 서빙과 같은 순위 기준)에서 뽑는다. 없으면 data."""
    ev_data = data if eval_data is None else eval_data
    folds = make_folds(data["date"], **fold_kw)
    if not folds:
        raise ValueError("walk-forward 폴드를 만들 데이터가 부족합니다")
    preds: list[pd.DataFrame] = []
    base: dict[str, list[pd.DataFrame]] = {"always_up": [], "momentum": [], "kospi": []}
    climatology: list[pd.DataFrame] = []  # 참고 기준: 학습 구간의 상승 비율을 그대로 확률로 말하기
    for f in folds:
        tr, va = (data[f.mask(data["date"], part)] for part in ("train", "valid"))
        te = ev_data[f.mask(ev_data["date"], "test")]
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
        climatology.append(pd.DataFrame({"p": tr["y_up"].mean(), "y": te["y_up"]}).dropna())
    allp = pd.concat(preds)
    clim = pd.concat(climatology)
    scored = allp.dropna(subset=["y_up"])
    brier_model, brier_clim = ev.brier(scored["p_up"], scored["y_up"]), ev.brier(clim["p"], clim["y"])
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
        "rank_returns": rank_returns(allp),
        # 예측력 점검: 학습 구간 상승 비율을 그대로 말하는 것보다 확률이 나은가 (> 0 이면 낫다)
        "skill": {
            "brier_model": brier_model,
            "brier_base_rate": brier_clim,
            "brier_skill": 1 - brier_model / brier_clim,
            "ic_p_up": ev.information_coefficient(
                scored["date"], scored["p_up"].to_numpy(), scored["ret_fwd"].to_numpy()
            ),
            "ic_positive_folds": _positive_ic_folds(scored),
            "folds": len(folds),
        },
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
    models.up_clf.save(d, "up_clf")
    models.out_clf.save(d, "out_clf")
    for k, b in models.quantiles.items():
        b.save_model(str(d / f"{k}.txt"))
    for name, cal in (("calibrator_up", models.calibrator_up), ("calibrator_out", models.calibrator_out)):
        if cal is not None:
            with open(d / f"{name}.pkl", "wb") as fh:
                pickle.dump(cal, fh)
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
    members: pd.DataFrame | None = None,
    eval_prices: pd.DataFrame | None = None,
    **fold_kw: int,
) -> Path:
    """전체 파이프라인: 데이터 -> 피처/타깃 -> walk-forward 평가 -> 최종 학습 -> 산출물 저장.
    확장 유니버스 학습: prices=전체 수집 종목, members=시점별 명단, eval_prices=서비스 종목
    (오프라인 성적은 서비스 종목·서빙과 같은 순위 기준으로 매긴다)."""
    params = {**PARAMS, **(params or {})}
    data = make_dataset(prices, index, macro, members)
    eval_data = make_dataset(eval_prices, index, macro) if eval_prices is not None else None
    metrics: dict[str, Any] = {"scope": "offline", "skipped": True}
    if not skip_eval:
        metrics, _ = walk_forward_eval(data, params, eval_data, **fold_kw)
    final_kw = {k: v for k, v in fold_kw.items() if k in ("train_days", "valid_days", "embargo")}
    models, fold = fit_final(data, params, **final_kw)
    dhash = data_hash(prices, index, macro, members, eval_prices)
    version = model_version(
        dhash, {**params, "ensemble_size": ENSEMBLE_SIZE}, now
    )  # 앙상블 크기도 버전에 반영
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
        "universe": "point-in-time 시총 상위 명단" if members is not None else "서비스 종목",
        "eval_universe": "서비스 종목" if eval_prices is not None else "학습과 같음",
        "n_rows": len(data),
        "data_hash": dhash,
        "horizon": HORIZON,
        "best_iterations": {
            "up_clf": models.up_clf.best_iteration,
            "out_clf": models.out_clf.best_iteration,
            **{k: b.best_iteration for k, b in models.quantiles.items()},
        },
        "calibrated": models.calibrator_up is not None,
        "ensemble_size": len(models.up_clf.boosters),
        "calibration": "platt",
        "up_target": "y_rel (같은 날 종목 중앙값 초과), P(up) 은 y_up 으로 Platt 보정",
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
    ap.add_argument(
        "--pit-universe",
        action="store_true",
        help="prices_daily_all.csv + universe_history.csv 로 학습하고 서비스 종목(prices_daily.csv)으로 평가",
    )
    a = ap.parse_args()
    if a.pit_universe:
        prices, index, macro = load_csv(a.data_dir, "prices_daily_all.csv")
        service, _, _ = load_csv(a.data_dir)
        d = run(
            prices,
            index,
            macro,
            a.out,
            skip_eval=a.skip_eval,
            members=load_members(a.data_dir),
            eval_prices=service,
        )
    else:
        prices, index, macro = load_csv(a.data_dir)
        d = run(prices, index, macro, a.out, skip_eval=a.skip_eval)
    print("저장:", d)


if __name__ == "__main__":  # pragma: no cover
    main()
