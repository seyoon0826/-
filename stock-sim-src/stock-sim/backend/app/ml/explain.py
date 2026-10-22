"""SHAP 기여도 TOP5 (FR-PR-5/6) -> prediction_factors 저장 구조.

P(up) 분류기 기준 SHAP 값(로그 오즈 단위)의 절댓값 상위 5개를
feature, value, shap_value, direction(+/-), rank, label(사람이 읽는 문장)로 돌려준다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import shap

from app.ml.features import label_for
from app.ml.infer import ModelBundle


class Explainer:
    def __init__(self, bundle: ModelBundle) -> None:
        self.bundle = bundle
        self._tree = shap.TreeExplainer(bundle.up_clf)

    def shap_values(self, X: pd.DataFrame) -> np.ndarray:
        sv = self._tree.shap_values(X[self.bundle.features])
        if isinstance(sv, list):  # 일부 버전은 이진 분류에서 [음성, 양성] 목록을 준다
            sv = sv[-1]
        return np.asarray(sv)

    def top_factors(self, X: pd.DataFrame, k: int = 5) -> list[list[dict[str, Any]]]:
        """행마다 SHAP 절댓값 상위 k 개."""
        sv = self.shap_values(X)
        feats = self.bundle.features
        out = []
        for i in range(len(X)):
            order = np.argsort(-np.abs(sv[i]))[:k]
            row = []
            for rank, j in enumerate(order, start=1):
                value = X.iloc[i][feats[j]]
                v = None if pd.isna(value) else float(value)
                row.append(
                    {
                        "rank": rank,
                        "feature": feats[j],
                        "value": v,
                        "shap_value": float(sv[i, j]),
                        "direction": "+" if sv[i, j] >= 0 else "-",
                        "label": label_for(feats[j], v),
                    }
                )
            out.append(row)
        return out
