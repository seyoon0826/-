"""시총 상위 30종목 일봉+수급을 CSV로 저장 (키 불필요).
실행: 프로젝트 루트에서  python scripts/collect_prices.py   (또는 make collect)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
from app.clients import pykrx_client as pk

OUT = Path(__file__).resolve().parents[1] / "data"
OUT.mkdir(exist_ok=True)
START, END = "20200101", pk.latest_business_day()

stocks = pk.top_marketcap(30)
stocks.to_csv(OUT / "stocks.csv", index=False, encoding="utf-8-sig")

frames = []
for code, name in zip(stocks["code"], stocks["name"]):
    print(f"수집 중: {name} ({code})")
    try:
        frames.append(pk.collect(code, START, END))
    except Exception as e:
        print(f"  실패: {e}")

pd.concat(frames).to_csv(OUT / "prices_daily.csv", encoding="utf-8-sig")
print("완료:", OUT / "prices_daily.csv")
