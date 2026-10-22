"""시간순 분할 검증 (SPEC 8.1). 랜덤 분할 금지.

학습 3년(756거래일) / 검증 3개월(63) / 테스트 3개월(63), 3개월(63거래일)씩 전진하는 롤링 윈도.
학습 끝과 검증 시작, 검증 끝과 테스트 시작 사이에 5거래일 간격(embargo)을 둔다.
간격 >= 예측 기간(5거래일)이라 학습 표본의 타깃(t+5)이 다음 구간으로 넘어가지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

TRAIN_DAYS, VALID_DAYS, TEST_DAYS, STEP_DAYS, EMBARGO = 756, 63, 63, 63, 5


@dataclass(frozen=True)
class Fold:
    index: int
    train: tuple[pd.Timestamp, pd.Timestamp]
    valid: tuple[pd.Timestamp, pd.Timestamp]
    test: tuple[pd.Timestamp, pd.Timestamp]

    def mask(self, dates: pd.Series, part: str) -> pd.Series:
        lo, hi = getattr(self, part)
        return (dates >= lo) & (dates <= hi)


def make_folds(
    dates: Sequence,
    train_days: int = TRAIN_DAYS,
    valid_days: int = VALID_DAYS,
    test_days: int = TEST_DAYS,
    step_days: int = STEP_DAYS,
    embargo: int = EMBARGO,
) -> list[Fold]:
    days = [pd.Timestamp(d) for d in sorted(pd.unique(pd.to_datetime(pd.Series(dates))))]
    folds = []
    start = 0
    while True:
        tr_end = start + train_days - 1
        va_start = tr_end + embargo + 1
        va_end = va_start + valid_days - 1
        te_start = va_end + embargo + 1
        te_end = te_start + test_days - 1
        if te_end >= len(days):
            break
        folds.append(
            Fold(
                len(folds),
                (days[start], days[tr_end]),
                (days[va_start], days[va_end]),
                (days[te_start], days[te_end]),
            )
        )
        start += step_days
    return folds
