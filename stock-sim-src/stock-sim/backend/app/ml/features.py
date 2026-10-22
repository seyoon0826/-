"""서빙용 피처. 학습 코드(ml/src/features.py)를 그대로 쓴다 — 학습-서빙 불일치 금지 (SPEC 8.2)."""

from src.features import (
    FEATURE_LABELS,
    FEATURES,
    HORIZON,
    build_features,
    label_for,
    news_feature_date,
)

from app.ml import ML_ROOT

__all__ = [
    "FEATURES",
    "FEATURE_LABELS",
    "HORIZON",
    "build_features",
    "label_for",
    "news_feature_date",
    "ML_ROOT",
]
