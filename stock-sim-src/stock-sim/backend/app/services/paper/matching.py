"""체결 계산 (SPEC 7.4). DB 를 모르는 순수 함수.

- 시장가 매수: 매도 1호가부터 잔량을 소진하며 위로. 10호가까지 다 써도 남으면 남은 수량은 마지막(10)호가 가격.
- 시장가 매도: 매수 1호가부터 아래로 동일.
- 지정가 매수: 지정가 이하 매도호가를 낮은 가격부터 잔량만큼. 남으면 대기(PARTIAL).
- 지정가 매도: 대칭.
- 체결가는 호가 가격이다(지정가보다 유리할 수 있음).
- 호가가 없으면 현재가에서 1틱 불리한 가격, 잔량 무제한.
- 호가 잔량은 주문끼리 공유 소진하지 않는다(각 주문이 독립적으로 호가를 본다, v1).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.services.market_data import BookLevel, OrderBook
from app.services.ticks import step_down, step_up

FALLBACK_LEVEL = 10  # 호가 없이 현재가 기준으로 체결한 경우의 단 번호
LEVEL_SLOTS = 16  # trades.event_seq = 호가 이벤트 seq x 16 + 단 번호


@dataclass(frozen=True)
class Fill:
    level: int  # 0 = 1호가
    price: int
    qty: int


def _walk(levels: tuple[BookLevel, ...], qty: int, ok: Callable[[int], bool], market: bool) -> list[Fill]:
    fills: list[Fill] = []
    left = qty
    for i, lv in enumerate(levels):
        if left <= 0:
            break
        if not market and not ok(lv.price):
            break
        take = min(left, lv.qty)
        if take > 0:
            fills.append(Fill(i, lv.price, take))
            left -= take
    if market and left > 0 and levels:
        last = len(levels) - 1
        if fills and fills[-1].level == last:
            prev = fills.pop()
            fills.append(Fill(last, prev.price, prev.qty + left))
        else:
            fills.append(Fill(last, levels[last].price, left))
    return fills


def match(
    side: str, order_type: str, qty: int, limit: int | None, book: OrderBook | None, last_price: int | None
) -> list[Fill]:
    if qty <= 0:
        return []
    market = order_type == "MARKET"
    levels = (book.asks if side == "BUY" else book.bids) if book else ()
    if levels:
        if side == "BUY":
            return _walk(levels, qty, lambda p: limit is not None and p <= limit, market)
        return _walk(levels, qty, lambda p: limit is not None and p >= limit, market)
    if last_price is None:
        return []
    price = step_up(last_price) if side == "BUY" else step_down(last_price)
    if (
        not market
        and limit is not None
        and ((side == "BUY" and price > limit) or (side == "SELL" and price < limit))
    ):
        return []
    return [Fill(FALLBACK_LEVEL, price, qty)]
