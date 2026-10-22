"""cash_ledger (SPEC 7.8). 계좌의 cash / reserved_cash 는 이 모듈의 post() 로만 바뀐다.

불변식(테스트로 검사): Σ(cash 영향 원장) == cash, Σ(reserved 영향 원장) == reserved_cash,
0 <= reserved_cash <= cash.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CashLedger, Order, PaperAccount, Position

RESERVED_TYPES = ("RESERVE", "RELEASE")
CASH_TYPES = ("DEPOSIT", "BUY", "SELL", "FEE", "TAX")


def post(
    session: Session,
    acct: PaperAccount,
    type_: str,
    amount: int,
    ref_type: str | None = None,
    ref_id: str | None = None,
) -> CashLedger | None:
    """amount 는 부호 포함. 0원 기록은 남기지 않는다."""
    if amount == 0:
        return None
    if type_ in RESERVED_TYPES:
        acct.reserved_cash += amount
        affects, balance = "reserved", acct.reserved_cash
    elif type_ in CASH_TYPES:
        acct.cash += amount
        affects, balance = "cash", acct.cash
    else:
        raise ValueError(f"알 수 없는 원장 유형: {type_}")
    row = CashLedger(
        account_id=acct.id,
        type=type_,
        affects=affects,
        amount=amount,
        balance_after=balance,
        ref_type=ref_type,
        ref_id=ref_id,
    )
    session.add(row)
    return row


def invariant_violations(session: Session, account_id: int) -> list[str]:
    """원장/예약 불변식 위반 목록 (비어 있으면 정상)."""
    acct = session.get(PaperAccount, account_id)
    if acct is None:
        return [f"계좌 {account_id} 없음"]
    errs: list[str] = []
    sums = dict(
        session.execute(
            select(CashLedger.affects, func.coalesce(func.sum(CashLedger.amount), 0))
            .where(CashLedger.account_id == account_id)
            .group_by(CashLedger.affects)
        ).all()
    )
    if int(sums.get("cash", 0)) != acct.cash:
        errs.append(f"cash {acct.cash} != 원장 {sums.get('cash', 0)}")
    if int(sums.get("reserved", 0)) != acct.reserved_cash:
        errs.append(f"reserved_cash {acct.reserved_cash} != 원장 {sums.get('reserved', 0)}")
    if not 0 <= acct.reserved_cash <= acct.cash:
        errs.append(f"0 <= reserved_cash({acct.reserved_cash}) <= cash({acct.cash}) 위반")
    active = ("PENDING", "PARTIAL")
    open_buy_reserve = session.scalar(
        select(func.coalesce(func.sum(Order.reserved_amount), 0)).where(
            Order.account_id == account_id, Order.side == "BUY", Order.status.in_(active)
        )
    )
    if int(open_buy_reserve or 0) != acct.reserved_cash:
        errs.append(f"미체결 매수 예약 합 {open_buy_reserve} != reserved_cash {acct.reserved_cash}")
    for pos in session.scalars(select(Position).where(Position.account_id == account_id)):
        open_sell = session.scalar(
            select(func.coalesce(func.sum(Order.qty - Order.filled_qty), 0)).where(
                Order.account_id == account_id,
                Order.code == pos.code,
                Order.side == "SELL",
                Order.status.in_(active),
            )
        )
        if int(open_sell or 0) != pos.reserved_qty:
            errs.append(f"{pos.code} reserved_qty {pos.reserved_qty} != 미체결 매도 {open_sell}")
    return errs
