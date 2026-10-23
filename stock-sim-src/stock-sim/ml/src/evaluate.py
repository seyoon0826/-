"""평가 지표 (SPEC 6.5 FR-PF-2/4): 방향 정확도, Brier, pinball loss, 범위 적중률, IC, 보정 곡선."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def direction_accuracy(p_up: np.ndarray, y_up: np.ndarray) -> float:
    return float(np.mean((np.asarray(p_up) >= 0.5) == (np.asarray(y_up) == 1)))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def pinball(q_pred: np.ndarray, y: np.ndarray, alpha: float) -> float:
    d = np.asarray(y, float) - np.asarray(q_pred, float)
    return float(np.mean(np.maximum(alpha * d, (alpha - 1) * d)))


def coverage(q10: np.ndarray, q90: np.ndarray, y: np.ndarray) -> float:
    y = np.asarray(y, float)
    return float(np.mean((y >= q10) & (y <= q90)))


def information_coefficient(
    dates: pd.Series, score: np.ndarray, realized: np.ndarray, min_n: int = 5
) -> float:
    """일별 순위상관(Spearman)의 평균. 종목 수가 min_n 미만인 날은 뺀다."""
    df = pd.DataFrame({"date": np.asarray(dates), "s": score, "r": realized})
    ics = []
    for _, g in df.groupby("date"):
        if len(g) >= min_n and g["s"].nunique() > 1 and g["r"].nunique() > 1:
            ics.append(g["s"].rank().corr(g["r"].rank()))
    return float(np.mean(ics)) if ics else float("nan")


def calibration_curve(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict[str, Any]]:
    """P(up) 을 10구간으로 나눠 구간별 실제 상승 비율 (FR-PF-4)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & ((p < hi) if i < bins - 1 else (p <= hi))
        out.append(
            {
                "bin": i,
                "lo": round(lo, 2),
                "hi": round(hi, 2),
                "p_mean": float(p[m].mean()) if m.any() else None,
                "actual": float(y[m].mean()) if m.any() else None,
                "n": int(m.sum()),
            }
        )
    return out


def metrics(pred: pd.DataFrame) -> dict[str, Any]:
    """pred: date, p_up, q10, q50, q90, y_up, ret_fwd 컬럼. 지표 dict."""
    pred = pred.dropna(subset=["y_up", "ret_fwd"])
    y, r = pred["y_up"].to_numpy(), pred["ret_fwd"].to_numpy()
    return {
        "direction_accuracy": direction_accuracy(pred["p_up"].to_numpy(), y),
        "brier": brier(pred["p_up"].to_numpy(), y),
        "pinball_q10": pinball(pred["q10"].to_numpy(), r, 0.1),
        "pinball_q50": pinball(pred["q50"].to_numpy(), r, 0.5),
        "pinball_q90": pinball(pred["q90"].to_numpy(), r, 0.9),
        "range_coverage": coverage(pred["q10"].to_numpy(), pred["q90"].to_numpy(), r),
        "ic": information_coefficient(pred["date"], pred["q50"].to_numpy(), r),
        "n": len(pred),
    }
