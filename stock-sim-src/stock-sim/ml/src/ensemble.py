"""시드 앙상블: 같은 설정의 이진 분류기를 시드만 바꿔 여러 개 학습하고 로그 오즈를 평균한다.

이유 (2026-10 진단): 행·피처 샘플링(bagging/feature_fraction) 때문에 모델 하나의 성적이 시드에 크게
흔들렸다 — 같은 데이터에서 시드 42 는 IC 0.001, 다른 시드는 0.015~0.026. 평균을 내면 이 운의 몫이 줄어든다.
SHAP 도 모델별 값의 평균이 평균 로그 오즈를 그대로 분해하므로(가법성) 근거 설명이 정확히 유지된다.
학습(ml)과 서빙(backend)이 이 클래스를 같이 쓴다.
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd


class SeedEnsemble:
    def __init__(self, boosters: list[lgb.Booster]) -> None:
        if not boosters:
            raise ValueError("빈 앙상블")
        self.boosters = list(boosters)

    def predict(self, X: pd.DataFrame, raw_score: bool = False) -> np.ndarray:
        raw = np.mean([b.predict(X, raw_score=True) for b in self.boosters], axis=0)
        return raw if raw_score else 1.0 / (1.0 + np.exp(-raw))

    @property
    def best_iteration(self) -> int:
        return int(np.median([b.best_iteration for b in self.boosters]))

    def save(self, directory: str | Path, name: str) -> None:
        for i, b in enumerate(self.boosters):
            b.save_model(str(Path(directory) / f"{name}_{i}.txt"))

    @classmethod
    def load(cls, directory: str | Path, name: str) -> SeedEnsemble:
        """{name}_0.txt, {name}_1.txt ... (예전 산출물의 단일 {name}.txt 도 읽는다)."""
        d = Path(directory)
        files = sorted(d.glob(f"{name}_[0-9]*.txt"), key=lambda p: int(p.stem.rsplit("_", 1)[1]))
        if not files and (d / f"{name}.txt").exists():
            files = [d / f"{name}.txt"]
        if not files:
            raise FileNotFoundError(f"{d}/{name}_*.txt")
        return cls([lgb.Booster(model_file=str(f)) for f in files])
