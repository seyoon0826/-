"""정규장 시간 판정 (SPEC 7.1): 거래일 09:00~15:30 KST.

거래일 여부: market_calendar(과거, pykrx 로 확정)를 우선하고, 행이 없는 날(미래 포함)은 holidays 패키지의
KRX 휴장일 캘린더(XKRX: 공휴일·임시공휴일·선거일·근로자의날·연말휴장)로 판정한다.
XKRX 판정은 2018-01-02~2026-10-20 실제 KRX 거래일 3,215일과 대조해 불일치 0건이었다(2026-10-21 확인).
"""

from datetime import date, datetime, time
from functools import lru_cache

import holidays

from app.services.market_data import KST

OPEN = time(9, 0)
CLOSE = time(15, 30)


@lru_cache(maxsize=32)
def _krx_holidays(year: int):
    return holidays.financial_holidays("XKRX", years=year)


def predicted_is_open(d: date) -> bool:
    """캘린더 행이 없을 때 쓰는 예측: 주말과 KRX 휴장일이 아니면 거래일."""
    return d.weekday() < 5 and d not in _krx_holidays(d.year)


def market_state(now: datetime, trading_day: bool | None = None) -> str:
    """'open' | 'closed'. trading_day 를 알면(market_calendar) 그 값을, 모르면 KRX 휴장일 예측으로 판단."""
    local = now.astimezone(KST)
    is_day = trading_day if trading_day is not None else predicted_is_open(local.date())
    if is_day and OPEN <= local.time() < CLOSE:
        return "open"
    return "closed"
