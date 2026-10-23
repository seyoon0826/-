"""학습용 확장 유니버스 수집 (생존 편향 제거 + 표본 확대). 서비스 유니버스(active 30종목)와는 별개다.

1) 2018-01 부터 매달 첫 거래일의 코스피 시총 상위 N 종목(우선주 제외)을 그 시점의 명단으로 기록한다.
   -> data/universe_history.csv (month, date, code, rank). '오늘의 상위 종목'으로 과거를 고르면 생기는
   생존 편향을 없애기 위해, 학습 때는 각 날짜에 그 시점 명단에 있던 종목만 쓴다.
2) 명단에 한 번이라도 든 종목의 일봉/수급/시총을 prices_daily 에 적재한다. 새 종목은 active=false 로 넣어
   서비스 화면에는 나오지 않는다. 이미 끝까지 받은 종목은 건너뛰므로 중간에 끊겨도 다시 실행하면 이어진다.

실행: 프로젝트 루트에서  python scripts/collect_training_universe.py [--top 100]
"""

import argparse
import logging
import socket
import sys
import time
from datetime import date
from pathlib import Path

socket.setdefaulttimeout(60)  # pykrx 요청에는 타임아웃이 없어 잠자기 뒤 응답 없는 연결에서 영원히 멈춘다
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import pandas as pd  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.clients import pykrx_client as pk  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.models import MarketCalendar, PriceDaily, Stock  # noqa: E402
from app.pipelines.collect_prices import BACKFILL_START, final_end_date, upsert_prices  # noqa: E402

log = logging.getLogger("training_universe")
OUT = ROOT / "data" / "universe_history.csv"
DONE = ROOT / "data" / "universe_done.txt"


def month_starts(end: date) -> list[date]:
    with SessionLocal() as s:
        days = list(
            s.scalars(
                select(MarketCalendar.date)
                .where(MarketCalendar.is_open.is_(True), MarketCalendar.date >= BACKFILL_START, MarketCalendar.date <= end)
                .order_by(MarketCalendar.date)
            )
        )
    firsts: dict[tuple[int, int], date] = {}
    for d in days:
        firsts.setdefault((d.year, d.month), d)
    return sorted(firsts.values())


def membership(dates: list[date], top: int) -> pd.DataFrame:
    done = pd.read_csv(OUT, dtype={"code": str}) if OUT.exists() else pd.DataFrame(columns=["date", "code", "rank"])
    have = set(done["date"].astype(str))
    rows = [done]
    for d in dates:
        if d.isoformat() in have:
            continue
        caps = pk._retry(pk._stock().get_market_cap_by_ticker, d.strftime("%Y%m%d"), market="KOSPI")
        caps = caps[[not pk.is_preferred(t) for t in caps.index]].sort_values("시가총액", ascending=False).head(top)
        rows.append(pd.DataFrame({"date": d.isoformat(), "code": list(caps.index), "rank": range(1, len(caps) + 1)}))
        log.info("%s 명단 %d종목", d, len(caps))
        pd.concat(rows).to_csv(OUT, index=False)  # 월마다 저장 (중단 대비)
    return pd.concat(rows, ignore_index=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    end = final_end_date(pk)  # 15:40 전이면 전 영업일까지 (장중 미확정 봉 제외)
    mem = membership(month_starts(end), a.top)
    codes = sorted(mem["code"].astype(str).str.zfill(6).unique())
    log.info("명단 %d개월, 종목 %d개", mem["date"].nunique(), len(codes))

    with SessionLocal() as s:
        known = set(s.scalars(select(Stock.code)))
        for c in codes:
            if c not in known:
                try:
                    name = pk._retry(pk._stock().get_market_ticker_name, c) or c
                except Exception:
                    name = c  # 상장폐지 종목 등 이름을 못 찾는 경우
                s.execute(insert(Stock).values(code=c, name=str(name)[:100], active=False).on_conflict_do_nothing())
        s.commit()

    # 끝까지 받은 종목 기록. 상장폐지 종목은 마지막 날짜가 end 보다 앞이라 날짜만으로는 완료를 알 수 없다.
    done = set(DONE.read_text().split()) if DONE.exists() else set()
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        if code in done:
            continue
        with SessionLocal() as s:
            last = s.scalar(select(func.max(PriceDaily.date)).where(PriceDaily.code == code))
        if last is None or last < end:
            try:
                df = pk.collect_full(code, BACKFILL_START.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
                with SessionLocal() as s:
                    n = upsert_prices(s, code, df) if len(df) else 0
                    s.commit()
                log.info("[%d/%d] %s %d행 (경과 %.0f분)", i, len(codes), code, n, (time.time() - t0) / 60)
            except Exception as e:
                log.warning("[%d/%d] %s 실패: %s", i, len(codes), code, e.__class__.__name__)
                continue
        with DONE.open("a") as fh:
            fh.write(code + "\n")
    log.info("DONE")


if __name__ == "__main__":
    main()
