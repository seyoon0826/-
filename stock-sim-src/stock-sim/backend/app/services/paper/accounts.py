"""계좌 생성/초기화 (FR-PT-1/2, FR-AI-1)와 평가: 포트폴리오(FR-PT-6), 성과(FR-PT-7)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import PaperAccount, PortfolioSnapshot, Position, Trade
from app.services.market_data import KST
from app.services.paper import errors as E
from app.services.paper import ledger
from app.services.paper.engine import Events, cancel_all_active, lock_account


def open_accounts(session: Session, user_id: int) -> tuple[PaperAccount | None, PaperAccount | None]:
    rows = session.scalars(
        select(PaperAccount).where(PaperAccount.user_id == user_id, PaperAccount.closed_at.is_(None))
    ).all()
    by_type = {a.type: a for a in rows}
    return by_type.get("USER"), by_type.get("AI")


def _new(session: Session, user_id: int, type_: str, initial_cash: int, now: datetime) -> PaperAccount:
    acct = PaperAccount(
        user_id=user_id, type=type_, initial_cash=initial_cash, cash=0, reserved_cash=0, created_at=now
    )
    session.add(acct)
    session.flush()
    ledger.post(session, acct, "DEPOSIT", initial_cash, "account", str(acct.id))
    return acct


def create_accounts(
    session: Session, user_id: int, initial_cash: int, now: datetime
) -> tuple[PaperAccount, PaperAccount]:
    """사용자 계좌 + 같은 초기자금/같은 시작일의 AI 계좌 (FR-PT-1, FR-AI-1)."""
    if initial_cash not in settings.paper_initial_cash_options:
        raise E.PaperError(E.INVALID_REQUEST, 422, "초기 자금은 1천만/3천만/5천만/1억 원 중에서 고르세요.")
    user, _ = open_accounts(session, user_id)
    if user is not None:
        raise E.PaperError(E.INVALID_REQUEST, 409, "이미 계좌가 있습니다. 초기화를 이용하세요.")
    return _new(session, user_id, "USER", initial_cash, now), _new(session, user_id, "AI", initial_cash, now)


def reset_accounts(
    session: Session, user_id: int, initial_cash: int | None, now: datetime, events: Events
) -> tuple[PaperAccount, PaperAccount]:
    """FR-PT-2: 모든 미체결 주문 취소, 포지션/원장은 보존한 채 이전 계좌를 닫고(closed_at) 새 계좌를 만든다.
    AI 계좌도 같은 시작일·초기자금으로 함께 새로 만든다 (FR-AI-1)."""
    user, ai = open_accounts(session, user_id)
    if user is None:
        raise E.PaperError(E.ACCOUNT_NOT_FOUND, 404)
    cash = initial_cash or user.initial_cash
    if cash not in settings.paper_initial_cash_options:
        raise E.PaperError(E.INVALID_REQUEST, 422, "초기 자금은 1천만/3천만/5천만/1억 원 중에서 고르세요.")
    for acct in (a for a in (user, ai) if a is not None):
        locked = lock_account(session, acct.id)
        cancel_all_active(session, locked, now, events)
        locked.closed_at = now
    session.flush()
    return _new(session, user_id, "USER", cash, now), _new(session, user_id, "AI", cash, now)


# ---------------------------------------------------------------- 포트폴리오 (FR-PT-6)
def portfolio(session: Session, acct: PaperAccount, prices: dict[str, int]) -> dict[str, Any]:
    """prices: 종목별 현재가 (없으면 평단으로 평가)."""
    holdings = []
    stock_value = 0
    for p in session.scalars(
        select(Position).where(Position.account_id == acct.id, Position.qty > 0).order_by(Position.code)
    ):
        price = prices.get(p.code) or int(p.avg_price)
        value = p.qty * price
        cost = p.avg_price * p.qty
        pnl = int((Decimal(value) - cost).to_integral_value(rounding=ROUND_HALF_UP))
        stock_value += value
        holdings.append(
            {
                "code": p.code,
                "qty": p.qty,
                "reserved_qty": p.reserved_qty,
                "avg_price": float(p.avg_price),
                "price": price,
                "value": value,
                "pnl": pnl,
                "return_pct": round(float((Decimal(value) - cost) / cost * 100), 2) if cost else 0.0,
            }
        )
    total = acct.cash + stock_value  # 예약금은 cash 안에 포함 (SPEC 7.7)
    return {
        "account_id": acct.id,
        "type": acct.type,
        "holdings": holdings,
        "cash": acct.cash,
        "reserved_cash": acct.reserved_cash,
        "available_cash": acct.cash - acct.reserved_cash,
        "stock_value": stock_value,
        "total_value": total,
        "initial_cash": acct.initial_cash,
        "return_pct": round((total - acct.initial_cash) / acct.initial_cash * 100, 2),
    }


# ---------------------------------------------------------------- 성과 (FR-PT-7)
@dataclass(frozen=True)
class SellResult:
    code: str
    pnl: int


def realized_sells(trades: list[Trade]) -> list[SellResult]:
    """체결 내역을 시간순으로 재생해 매도 건별 실현손익을 구한다 (승률용, 규칙은 엔진과 동일)."""
    qty: dict[str, int] = defaultdict(int)
    avg: dict[str, Decimal] = defaultdict(Decimal)
    out: list[SellResult] = []
    for t in sorted(trades, key=lambda x: (x.filled_at, x.id)):
        if t.side == "BUY":
            new_qty = qty[t.code] + t.qty
            avg[t.code] = ((qty[t.code] * avg[t.code] + Decimal(t.amount)) / new_qty).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            qty[t.code] = new_qty
        else:
            pnl = (Decimal(t.price) - avg[t.code]) * t.qty - t.fee - t.tax
            out.append(SellResult(t.code, int(pnl.to_integral_value(rounding=ROUND_HALF_UP))))
            qty[t.code] -= t.qty
    return out


def max_drawdown(values: list[int]) -> float:
    peak, mdd = 0, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak)
    return round(mdd * 100, 2)


def performance(session: Session, acct: PaperAccount) -> dict[str, Any]:
    snaps = session.scalars(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.account_id == acct.id)
        .order_by(PortfolioSnapshot.date)
    ).all()
    trades = list(session.scalars(select(Trade).where(Trade.account_id == acct.id)))
    sells = realized_sells(trades)
    wins = sum(1 for s in sells if s.pnl > 0)
    traded = sum(t.amount for t in trades)
    avg_value = sum(s.total_value for s in snaps) / len(snaps) if snaps else acct.initial_cash
    return {
        "account_id": acct.id,
        "type": acct.type,
        "curve": [
            {
                "date": s.date.isoformat(),
                "total_value": s.total_value,
                "return_pct": float(s.return_pct),
                "kospi_return_pct": float(s.kospi_return_pct) if s.kospi_return_pct is not None else None,
            }
            for s in snaps
        ],
        "mdd_pct": max_drawdown([s.total_value for s in snaps]),
        "win_rate": round(wins / len(sells) * 100, 2) if sells else None,  # 실현 매도 기준
        "sell_count": len(sells),
        "trade_count": len(trades),
        # 회전율 = (매수+매도 체결금액 / 2) / 평균 총자산
        "turnover": round(traded / 2 / avg_value, 4) if avg_value else 0.0,
    }


def kst_today(now: datetime) -> date:
    return now.astimezone(KST).date()
