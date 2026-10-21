"""기준가 주변으로 10단 호가를 합성한다 (Provider 가 호가를 못 줄 때)."""

from datetime import datetime

from app.services.market_data import DEPTH, BookLevel, OrderBook
from app.services.ticks import step_down, step_up


def synth_orderbook(code: str, ref_price: int, qty: int, ts: datetime, source: str) -> OrderBook:
    """매도 1호가 = 기준가 + 1틱, 매수 1호가 = 기준가 - 1틱, 이후 1틱씩 벌어진다 (FR-MD-2).
    구간 경계(예: 5,000원)를 넘으면 틱 크기가 바뀌는 것을 반영한다."""
    asks, bids = [], []
    a = b = ref_price
    for _ in range(DEPTH):
        a = step_up(a)
        b = step_down(b)
        asks.append(BookLevel(a, qty))
        if b > 0:
            bids.append(BookLevel(b, qty))
    return OrderBook(code, tuple(asks), tuple(bids), ts, source)
