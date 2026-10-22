"""DB -> 학습용 CSV (data/*.csv). Colab 학습 전에 실행해 Drive 에 올린다.
실행: 프로젝트 루트에서  python scripts/export_training_data.py   (또는 make export-data)"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import engine  # noqa: E402

OUT = ROOT / "data"
QUERIES = {
    "prices_daily.csv": "SELECT code, date, open, high, low, close, volume, value, foreign_net, inst_net, market_cap "
    "FROM prices_daily ORDER BY code, date",
    "index_daily.csv": "SELECT index_code, date, close FROM index_daily ORDER BY index_code, date",
    "macro_daily.csv": "SELECT series, date, value FROM macro_daily ORDER BY series, date",
    "stocks.csv": "SELECT code, name, active FROM stocks ORDER BY code",
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with engine.connect() as conn:
        for name, sql in QUERIES.items():
            df = pd.read_sql(text(sql), conn)
            df.to_csv(OUT / name, index=False)
            span = f"{df['date'].min()} ~ {df['date'].max()}" if "date" in df and len(df) else ""
            print(f"{name}: {len(df):,}행 {span}")
    print("저장:", OUT, "-> 이 폴더를 Google Drive 의 StockSim/data 에 올리세요.")


if __name__ == "__main__":
    main()
