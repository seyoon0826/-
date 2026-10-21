"""최초 1회 백필: 캘린더 -> 유니버스 -> 가격/수급/지수 -> (키가 있으면) 거시/공시/뉴스.

실행: make backfill   (또는 cd backend && python -m app.pipelines.backfill)
모든 단계가 멱등이라 중간에 끊겨도 다시 실행하면 이어서 채운다.
"""

import logging
from datetime import datetime

from dotenv import load_dotenv

from app.pipelines import collect_prices, market_calendar, scheduler


def main() -> None:
    load_dotenv("../.env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from app.clients import pykrx_client as pk

    end = datetime.strptime(pk.latest_business_day(), "%Y%m%d").date()
    if market_calendar.known_dates() == 0:
        market_calendar.refresh(scheduler.CALENDAR_START, end)
    if not collect_prices.active_codes():
        collect_prices.select_universe(end)
    scheduler.catch_up()  # 가격 -> 거시 -> 공시 -> 뉴스 (키가 없는 단계는 건너뜀)


if __name__ == "__main__":
    main()
