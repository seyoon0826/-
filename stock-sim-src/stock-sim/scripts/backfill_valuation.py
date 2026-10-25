"""prices_daily 의 PER/PBR/배당수익률 채우기 (마이그레이션 0004 이후 1회). 일일 수집은 collect_full 이 함께 받는다.

실행: 프로젝트 루트에서
  python scripts/backfill_valuation.py                 # pykrx 로 종목마다 2018년부터 조회 (종목당 수십 초)
  python scripts/backfill_valuation.py --from-dir DIR  # 미리 받아 둔 CSV(date, per, pbr, div, code) 적재
이미 값이 있는 종목은 건너뛴다(이어받기). 멱등.
"""

import argparse
import logging
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
socket.setdefaulttimeout(60)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import pandas as pd  # noqa: E402
from sqlalchemy import bindparam, func, select, update  # noqa: E402

from app.clients import pykrx_client as pk  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.models import PriceDaily  # noqa: E402
from app.pipelines.collect_prices import (
    BACKFILL_START,
    _dec,
    final_end_date,
)  # noqa: E402

log = logging.getLogger("backfill_valuation")


def frame_for(code: str, src: Path | None, end: str) -> pd.DataFrame:
    if src is not None:
        f = src / f"{code}.csv"
        if not f.exists():
            return pd.DataFrame()
        df = pd.read_csv(f).rename(columns={"div": "div_yield"})
        return df.set_index(pd.to_datetime(df["date"]).dt.date)[
            ["per", "pbr", "div_yield"]
        ]
    parts = [
        pk.valuation(code, s, e)
        for s, e in pk._year_windows(BACKFILL_START.strftime("%Y%m%d"), end)
    ]
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts)
    df.index = pd.to_datetime(df.index).date
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-dir", type=Path)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    end = final_end_date(pk).strftime("%Y%m%d") if a.from_dir is None else ""
    with SessionLocal() as s:
        codes = list(
            s.scalars(select(PriceDaily.code).distinct().order_by(PriceDaily.code))
        )
        done = set(
            s.scalars(
                select(PriceDaily.code).where(PriceDaily.per.is_not(None)).distinct()
            )
        )
    todo = [c for c in codes if c not in done]
    log.info("대상 %d종목 (이미 있음 %d)", len(todo), len(done))
    stmt = (
        update(PriceDaily)
        .where(PriceDaily.code == bindparam("c"), PriceDaily.date == bindparam("d"))
        .values(per=bindparam("per"), pbr=bindparam("pbr"), div_yield=bindparam("dy"))
    )
    for i, code in enumerate(todo, 1):
        try:
            df = frame_for(code, a.from_dir, end)
        except Exception as e:  # noqa: BLE001
            log.warning("[%d/%d] %s 실패: %s", i, len(todo), code, e.__class__.__name__)
            continue
        rows = [
            {
                "c": code,
                "d": d,
                "per": _dec(r["per"]),
                "pbr": _dec(r["pbr"]),
                "dy": _dec(r["div_yield"]),
            }
            for d, r in df.iterrows()
        ]
        if rows:
            with SessionLocal() as s:
                s.connection().execute(stmt, rows)
                s.commit()
        log.info("[%d/%d] %s %d행", i, len(todo), code, len(rows))
    with SessionLocal() as s:
        n = s.scalar(select(func.count()).where(PriceDaily.per.is_not(None)))
    log.info("DONE: PER 이 있는 행 %d", n)


if __name__ == "__main__":
    main()
