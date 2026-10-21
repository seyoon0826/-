"""정규장 시간 판정 (SPEC 7.1): 거래일 09:00~15:30 KST."""

from datetime import datetime, time

from app.services.market_data import KST

OPEN = time(9, 0)
CLOSE = time(15, 30)


def market_state(now: datetime, trading_day: bool | None = None) -> str:
    """'open' | 'closed'. trading_day 를 알면(market_calendar) 그 값을, 모르면 평일 여부로 판단."""
    local = now.astimezone(KST)
    is_day = trading_day if trading_day is not None else local.weekday() < 5
    if is_day and OPEN <= local.time() < CLOSE:
        return "open"
    return "closed"
