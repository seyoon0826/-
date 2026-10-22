"""수수료/거래세 (SPEC 7.5). 요율은 fee_config 에서 체결일 기준 유효한 행을 읽는다. 하드코딩 금지.

모든 금액은 정수(원), 원 단위 절사(floor). 매수 예약금은 올림(ceil)으로 넉넉히 잡는다 (SPEC 7.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FeeConfig


class FeeConfigMissing(RuntimeError):
    pass


@dataclass(frozen=True)
class FeeRates:
    buy_fee: Decimal
    sell_fee: Decimal
    sell_tax: Decimal


def rates_on(session: Session, d: date) -> FeeRates:
    row = session.scalars(
        select(FeeConfig)
        .where(FeeConfig.effective_from <= d)
        .order_by(FeeConfig.effective_from.desc())
        .limit(1)
    ).first()
    if row is None:
        raise FeeConfigMissing(f"{d} 에 유효한 fee_config 가 없습니다")
    return FeeRates(Decimal(row.buy_fee_rate), Decimal(row.sell_fee_rate), Decimal(row.sell_tax_rate))


def _floor(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_FLOOR))


def buy_fee(amount: int, r: FeeRates) -> int:
    return _floor(Decimal(amount) * r.buy_fee)


def sell_costs(amount: int, r: FeeRates) -> tuple[int, int]:
    """(수수료, 거래세)"""
    return _floor(Decimal(amount) * r.sell_fee), _floor(Decimal(amount) * r.sell_tax)


def buy_reserve(price: int, qty: int, r: FeeRates) -> int:
    """매수 필요금액 = 가격 x 수량 x (1 + 매수수수료율), 올림."""
    if qty <= 0:
        return 0
    return int((Decimal(price) * qty * (1 + r.buy_fee)).to_integral_value(rounding=ROUND_CEILING))
