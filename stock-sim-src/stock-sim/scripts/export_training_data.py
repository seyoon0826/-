"""DB -> 학습용 CSV (data/*.csv). Colab 학습 전에 실행해 Drive 에 올린다.
실행: 프로젝트 루트에서  python scripts/export_training_data.py [--all]   (또는 make export-data)

prices_daily.csv 는 서비스 종목(active)만 담는다. 종목 간 순위 피처(_xr)를 서빙과 같은 종목 집합에서
계산해야 하기 때문이다. --all 은 확장 학습 유니버스(collect_training_universe.py)까지 담은
prices_daily_all.csv 를 추가로 쓴다 (실험용)."""

import argparse
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
PRICES = (
    "SELECT p.code, p.date, p.open, p.high, p.low, p.close, p.volume, p.value, p.foreign_net, p.inst_net, "
    "p.market_cap, p.per, p.pbr, p.div_yield FROM prices_daily p JOIN stocks s ON s.code = p.code {where} ORDER BY p.code, p.date"
)
QUERIES = {
    "prices_daily.csv": PRICES.format(where="WHERE s.active"),
    "index_daily.csv": "SELECT index_code, date, close FROM index_daily ORDER BY index_code, date",
    "macro_daily.csv": "SELECT series, date, value FROM macro_daily ORDER BY series, date",
    "stocks.csv": "SELECT code, name, active FROM stocks ORDER BY code",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="확장 학습 유니버스 prices_daily_all.csv 도 쓴다")
    queries = dict(QUERIES)
    if ap.parse_args().all:
        queries["prices_daily_all.csv"] = PRICES.format(where="")
    OUT.mkdir(exist_ok=True)
    with engine.connect() as conn:
        for name, sql in queries.items():
            df = pd.read_sql(text(sql), conn)
            df.to_csv(OUT / name, index=False)
            span = f"{df['date'].min()} ~ {df['date'].max()}" if "date" in df and len(df) else ""
            print(f"{name}: {len(df):,}행 {span}")
    print("저장:", OUT, "-> 이 폴더를 Google Drive 의 StockSim/data 에 올리세요.")


if __name__ == "__main__":
    main()
