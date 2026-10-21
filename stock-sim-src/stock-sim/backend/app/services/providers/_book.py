"""기준가 주변으로 10단 호가를 합성한다 (Provider 가 호가를 못 줄 때)."""

from datetime import datetime

from app.services.market_data import DEPTH, BookLevel, OrderBook
from app.services.ticks import round_down_to_tick, round_up_to_tick, step_down, step_up


def synth_orderbook(code: str, ref_price: int, qty: int, ts: datetime, source: str) -> OrderBook:
    """매도 1호가 = 기준가 + 1틱, 매수 1호가 = 기준가 - 1틱, 이후 1틱씩 벌어진다 (FR-MD-2).
    구간 경계(예: 5,000원)를 넘으면 틱 크기가 바뀌는 것을 반영한다.
    기준가가 호가 단위에 안 맞으면(과거 수정주가, 예: 11,703원) 기준가보다 큰/작은 가장 가까운 유효 호가에서
    시작해 모든 호가가 주문 가능한 가격이 되게 한다."""
    asks, bids = [], []
    a, b = round_down_to_tick(ref_price), round_up_to_tick(ref_price)
    for _ in range(DEPTH):
        a = step_up(a)
        b = step_down(b)
        asks.append(BookLevel(a, qty))
        if b > 0:
            bids.append(BookLevel(b, qty))
    return OrderBook(code, tuple(asks), tuple(bids), ts, source)
