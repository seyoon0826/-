"""합성 시장 데이터 (테스트/파이프라인 검증용). 실제 데이터 없이 학습->산출물->서빙->추론을 끝까지 돌린다.

약한 신호를 심는다: 다음 5일 수익률이 외국인 순매수와 최근 모멘텀에 조금씩 비례한다(모델이 배울 거리).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_synthetic(
    n_codes: int = 12, n_days: int = 1_000, seed: int = 7, signal_strength: float = 0.002
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2019-01-02", periods=n_days)
    mkt = rng.normal(0.0003, 0.01, n_days)
    kospi = 2500 * np.cumprod(1 + mkt)
    rows = []
    for i in range(n_codes):
        code = f"{100000 + i * 10:06d}"
        flow = rng.normal(0, 1, n_days)
        signal = np.zeros(n_days)
        signal[1:] = signal_strength * flow[:-1]  # 어제 수급이 오늘 수익률에 반영 (학습 가능한 신호)
        r = mkt * rng.uniform(0.6, 1.4) + signal + rng.normal(0, 0.015, n_days)
        close = np.round(20_000 * (i + 1) * np.cumprod(1 + r))
        high = np.round(close * (1 + rng.uniform(0, 0.02, n_days)))
        low = np.round(close * (1 - rng.uniform(0, 0.02, n_days)))
        openp = np.round((high + low) / 2)
        vol = rng.integers(100_000, 1_000_000, n_days).astype(float)
        vol[rng.random(n_days) < 0.003] = 0  # 가끔 거래정지
        cap = close * 1e7
        foreign = flow * cap * 1e-4
        foreign[vol == 0] = 0
        foreign_nan = foreign.copy()
        foreign_nan[rng.random(n_days) < 0.01] = np.nan  # 가끔 진짜 결측
        rows.append(
            pd.DataFrame(
                {
                    "code": code,
                    "date": days,
                    "open": openp,
                    "high": np.maximum(high, close),
                    "low": np.minimum(low, close),
                    "close": close,
                    "volume": vol,
                    "value": close * vol,
                    "foreign_net": foreign_nan,
                    "inst_net": rng.normal(0, 1, n_days) * cap * 1e-4,
                    "market_cap": cap,
                }
            )
        )
    prices = pd.concat(rows, ignore_index=True)
    index = pd.DataFrame({"index_code": "KOSPI", "date": days, "close": kospi})
    macro = pd.concat(
        [
            pd.DataFrame(
                {"series": "usdkrw", "date": days, "value": 1300 + np.cumsum(rng.normal(0, 3, n_days))}
            ),
            pd.DataFrame({"series": "base_rate", "date": days[::60], "value": 3.5}),
        ]
    )
    return prices, index, macro
