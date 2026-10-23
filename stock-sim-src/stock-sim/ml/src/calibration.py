"""확률 보정 (SPEC 8.3 SHOULD). 모델 점수 -> 실제 확률.

Platt(로지스틱) 보정: 검증 구간에서 logit(점수) 로 실제 결과(y)를 맞추는 1차 로지스틱 회귀.
isotonic 보정은 약한 모델에서 값을 몇 개의 계단으로 뭉쳐(모든 종목이 같은 확률) 쓰지 않는다.
"""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p)).reshape(-1, 1)


class PlattCalibrator:
    def __init__(self) -> None:
        self.lr = LogisticRegression(C=1.0)

    def fit(self, scores: np.ndarray, y: np.ndarray) -> PlattCalibrator:
        self.lr.fit(_logit(scores), np.asarray(y, int))
        return self

    def predict(self, scores: np.ndarray) -> np.ndarray:
        return self.lr.predict_proba(_logit(scores))[:, 1]
