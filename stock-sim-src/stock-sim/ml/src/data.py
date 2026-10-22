"""학습 데이터 입출력. DB 덤프(scripts/export_training_data.py)가 만든 CSV 를 읽는다 (Colab 에서도 동일)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

FILES = {"prices": "prices_daily.csv", "index": "index_daily.csv", "macro": "macro_daily.csv"}


def load_csv(data_dir: str | Path) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None]:
    d = Path(data_dir)
    prices = pd.read_csv(d / FILES["prices"], dtype={"code": str}, parse_dates=["date"])
    prices["code"] = prices["code"].str.zfill(6)
    index = pd.read_csv(d / FILES["index"], parse_dates=["date"]) if (d / FILES["index"]).exists() else None
    if index is not None and "index_code" in index:
        index = index[index["index_code"] == "KOSPI"]
    macro = pd.read_csv(d / FILES["macro"], parse_dates=["date"]) if (d / FILES["macro"]).exists() else None
    return prices, index, macro


def data_hash(*frames: pd.DataFrame | None) -> str:
    """재현성 기록용 데이터 해시 (SPEC 14). 행 순서와 무관하게 같은 데이터면 같은 값."""
    h = hashlib.sha256()
    for f in frames:
        if f is None:
            h.update(b"none")
            continue
        cols = sorted(f.columns)
        s = f[cols].sort_values(cols[:2] if len(cols) > 1 else cols).to_csv(index=False)
        h.update(s.encode())
    return h.hexdigest()
