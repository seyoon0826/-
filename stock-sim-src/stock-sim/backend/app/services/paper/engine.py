"""체결 엔진 (SPEC 7): 검증 -> 자금/수량 예약 -> 체결 대기 -> 체결 -> 잔고/포지션 반영.

트랜잭션/동시성 (SPEC 7.9) — **DB 락에만 의존**하는 방식을 택했다.
- 주문 생성, 취소, 체결 처리는 각각 호출자의 한 트랜잭션 안에서 실행된다(이 모듈은 commit 하지 않는다).
- 시작 시 계좌 행을 `SELECT ... FOR UPDATE` 로 잠근다. 잠금 순서는 항상 계좌 -> 주문이라 교착이 생기지 않는다.
- 체결 처리는 주문 행도 FOR UPDATE 로 잠그고, 종료 상태면 즉시 반환한다(멱등).
- 같은 호가 이벤트로 두 번 체결되지 않도록 trades (order_id, event_seq) 를 먼저 확인한다.
  유니크 제약이 최종 방어선이다.
  event_seq = 호가 이벤트 seq x 16 + 호가 단 번호 (한 주문이 여러 호가에 걸치면 trades 가 여러 행).
- AI 계정도 이 함수들을 그대로 쓴다. 특별 경로 없음 (FR-AI-3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import MarketCalendar, Order, PaperAccount, Position, Stock, Trade
from app.services.market_data import KST, OrderBook
from app.services.market_hours import CLOSE as MARKET_CLOSE
from app.services.market_hours import market_state, predicted_is_open
from app.services.market_store import is_stale
from app.services.paper import errors as E
from app.services.paper import ledger
from app.services.paper.fees import FeeRates, buy_fee, buy_reserve, rates_on, sell_costs
from app.services.paper.matching import LEVEL_SLOTS, Fill, match
from app.services.ticks import is_valid_tick, price_limits

ACTIVE = ("PENDING", "PARTIAL")
TERMINAL = ("FILLED", "CANCELED", "EXPIRED", "REJECTED")
REALTIME_SOURCES = (
    "kis",
)  # 시세 신선도(30초) 검사를 하는 공급원. pykrx(종가)/replay 는 대상 아님 (SPEC 7.1)


@dataclass(frozen=True)
class OrderRequest:
    code: str
    side: str  # BUY | SELL
    order_type: str  # MARKET | LIMIT
    qty: int
    limit_price: int | None = None
    idempotency_key: str | None = None
    source: str = "USER"  # USER | AI
    prediction_id: int | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    """주문/체결 판단에 쓰는 시세. 호출자가 Redis(또는 DB)에서 읽어 넘긴다."""

    book: OrderBook | None
    last_price: int | None
    quote_ts: datetime | None
    source: str | None
    prev_close: int | None  # 가격제한폭/시장가 예약 기준가 (전일 종가)


@dataclass
class Events:
    """커밋 후 WS 로 내보낼 이벤트 (ch:paper:{account_id})."""

    items: list[tuple[int, dict[str, Any]]] = field(default_factory=list)

    def order(self, o: Order) -> None:
        self.items.append((o.account_id, {"type": "order_update", "order": order_dict(o)}))

    def trade(self, t: Trade) -> None:
        self.items.append((t.account_id, {"type": "trade", "trade": trade_dict(t)}))


def order_dict(o: Order) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "code": o.code,
        "side": o.side,
        "order_type": o.order_type,
        "qty": o.qty,
        "filled_qty": o.filled_qty,
        "limit_price": o.limit_price,
        "reserved_amount": o.reserved_amount,
        "status": o.status,
        "reject_reason": o.reject_reason,
        "queued": o.queued,
        "source": o.source,
        "prediction_id": o.prediction_id,
        "created_at": o.created_at.astimezone(KST).isoformat() if o.created_at else None,
        "updated_at": o.updated_at.astimezone(KST).isoformat() if o.updated_at else None,
    }


def trade_dict(t: Trade) -> dict[str, Any]:
    return {
        "id": t.id,
        "order_id": str(t.order_id),
        "code": t.code,
        "side": t.side,
        "qty": t.qty,
        "price": t.price,
        "amount": t.amount,
        "fee": t.fee,
        "tax": t.tax,
        "filled_at": t.filled_at.astimezone(KST).isoformat() if t.filled_at else None,
    }


# ---------------------------------------------------------------- 공통
def is_trading_day(session: Session, d: date) -> bool:
    row = session.get(MarketCalendar, d)
    return row.is_open if row else predicted_is_open(d)


def is_market_open(session: Session, now: datetime) -> bool:
    if settings.paper_ignore_market_hours:
        return True
    return market_state(now, is_trading_day(session, now.astimezone(KST).date())) == "open"


def order_session_date(session: Session, created_at: datetime) -> date:
    """DAY 주문이 속한 거래일 (SPEC 7.1). 거래일 15:30 전(개장 전 포함)에 접수하면 그날,
    장 마감 후나 휴장일에 접수하면 다음 거래일 장에서 처리된다."""
    local = created_at.astimezone(KST)
    d = local.date()
    if is_trading_day(session, d) and local.time() < MARKET_CLOSE:
        return d
    for _ in range(30):
        d += timedelta(days=1)
        if is_trading_day(session, d):
            return d
    return d


def is_expired(session: Session, order: Order, now: datetime) -> bool:
    """주문이 속한 장이 이미 끝났는지.
    스케줄러(15:30 만료 작업)가 멈춰 있어도 엔진이 지난 장의 주문을 체결하지 않게 한다."""
    day = order_session_date(session, order.created_at)
    local = now.astimezone(KST)
    return local.date() > day or (local.date() == day and local.time() >= MARKET_CLOSE)


def lock_account(session: Session, account_id: int) -> PaperAccount:
    acct = session.scalars(
        select(PaperAccount).where(PaperAccount.id == account_id).with_for_update()
    ).first()
    if acct is None or acct.closed_at is not None:
        raise E.PaperError(E.ACCOUNT_NOT_FOUND, 404)
    return acct


def _lock_order(session: Session, order_id: UUID) -> Order | None:
    return session.scalars(select(Order).where(Order.id == order_id).with_for_update()).first()


def _position(session: Session, account_id: int, code: str) -> Position:
    pos = session.get(Position, (account_id, code))
    if pos is None:
        pos = Position(
            account_id=account_id, code=code, qty=0, reserved_qty=0, avg_price=Decimal("0"), realized_pnl=0
        )
        session.add(pos)
        session.flush()
    return pos


def _ref(o: Order) -> str:
    return str(o.id)


# ---------------------------------------------------------------- 검증 (SPEC 7.2: 순서대로, 첫 실패에서 거부)
def _validate(
    session: Session,
    acct: PaperAccount,
    req: OrderRequest,
    mkt: MarketSnapshot,
    rates: FeeRates,
    now: datetime,
) -> tuple[str | None, int, int | None]:
    """(에러코드, 매수 예약금, 예약 기준가)"""
    stock = session.get(Stock, req.code)
    if stock is None or not stock.active:
        return E.UNKNOWN_SYMBOL, 0, None
    if (
        not isinstance(req.qty, int)
        or isinstance(req.qty, bool)
        or not 1 <= req.qty <= settings.paper_max_order_qty
    ):
        return E.INVALID_QTY, 0, None
    if req.order_type == "LIMIT":
        if req.limit_price is None or not is_valid_tick(req.limit_price):
            return E.INVALID_TICK, 0, None
        if mkt.prev_close:
            low, high = price_limits(mkt.prev_close)
            if not low <= req.limit_price <= high:
                return E.PRICE_OUT_OF_LIMIT, 0, None
    reserve, reserve_price = 0, None
    if req.side == "BUY":
        if req.order_type == "LIMIT":
            reserve_price = req.limit_price
        elif mkt.prev_close:
            reserve_price = price_limits(mkt.prev_close)[1]  # 시장가는 상한가로 예약 후 체결 시 차액 해제
        else:
            return E.UPSTREAM_UNAVAILABLE, 0, None
        assert reserve_price is not None
        reserve = buy_reserve(reserve_price, req.qty, rates)
        if acct.cash - acct.reserved_cash < reserve:
            return E.INSUFFICIENT_CASH, 0, None
    else:
        pos = session.get(Position, (acct.id, req.code))
        if pos is None or pos.qty - pos.reserved_qty < req.qty:
            return E.INSUFFICIENT_QTY, 0, None
    if req.order_type == "MARKET" and is_market_open(session, now) and mkt.source in REALTIME_SOURCES:
        # SPEC 14: 시세가 30초 넘게 오래되면 시장가 거부
        if mkt.quote_ts is None or is_stale(mkt.quote_ts, now, settings.quote_stale_sec):
            return E.UPSTREAM_UNAVAILABLE, 0, None
    return None, reserve, reserve_price


# ---------------------------------------------------------------- 주문 생성
def place_order(
    session: Session, account_id: int, req: OrderRequest, mkt: MarketSnapshot, now: datetime, events: Events
) -> Order:
    """주문을 접수한다. 검증 실패도 REJECTED 주문으로 저장해 반환한다(호출자가 422 로 변환).
    같은 Idempotency-Key 재요청이면 기존 주문을 그대로 반환한다."""
    acct = lock_account(session, account_id)
    if req.idempotency_key:
        prev = session.scalars(
            select(Order).where(Order.account_id == acct.id, Order.idempotency_key == req.idempotency_key)
        ).first()
        if prev is not None:
            return prev
    if req.side not in ("BUY", "SELL") or req.order_type not in ("MARKET", "LIMIT"):
        raise E.PaperError(E.INVALID_REQUEST)

    rates = rates_on(session, now.astimezone(KST).date())
    error, reserve, reserve_price = _validate(session, acct, req, mkt, rates, now)
    is_open = is_market_open(session, now)
    order = Order(
        account_id=acct.id,
        code=req.code,
        side=req.side,
        order_type=req.order_type,
        qty=req.qty if isinstance(req.qty, int) else 0,
        filled_qty=0,
        limit_price=req.limit_price if req.order_type == "LIMIT" else None,
        reserve_price=reserve_price,
        reserved_amount=0,
        status="REJECTED" if error else "PENDING",
        reject_reason=error,
        queued=not is_open and error is None,
        source=req.source,
        prediction_id=req.prediction_id,
        idempotency_key=req.idempotency_key,
        created_at=now,
        updated_at=now,
    )
    session.add(order)
    session.flush()
    if error:
        events.order(order)
        return order

    if req.side == "BUY":
        order.reserved_amount = reserve
        ledger.post(session, acct, "RESERVE", reserve, "order", _ref(order))
    else:
        pos = _position(session, acct.id, req.code)
        pos.reserved_qty += req.qty
    if is_open:
        _try_fill(session, acct, order, mkt, now, events)
    events.order(order)
    return order


# ---------------------------------------------------------------- 체결
def _already_processed(session: Session, order: Order, seq: int) -> bool:
    lo, hi = seq * LEVEL_SLOTS, seq * LEVEL_SLOTS + LEVEL_SLOTS - 1
    return (
        session.scalars(
            select(Trade.id).where(Trade.order_id == order.id, Trade.event_seq.between(lo, hi)).limit(1)
        ).first()
        is not None
    )


def _apply_fill(
    session: Session, acct: PaperAccount, order: Order, fill: Fill, seq: int, now: datetime, events: Events
) -> None:
    rates = rates_on(session, now.astimezone(KST).date())  # 수수료/세금은 체결일 기준 요율
    amount = fill.price * fill.qty
    pos = _position(session, acct.id, order.code)
    ref = _ref(order)
    fee, tax = 0, 0
    if order.side == "BUY":
        fee = buy_fee(amount, rates)
        ledger.post(session, acct, "BUY", -amount, "order", ref)
        ledger.post(session, acct, "FEE", -fee, "order", ref)
        # 남은 수량의 예약금을 다시 계산해 차액만 해제한다 (남은 예약은 유지, SPEC 7.7)
        remaining = order.qty - order.filled_qty - fill.qty
        reserve_rates = rates_on(session, order.created_at.astimezone(KST).date())
        new_reserved = buy_reserve(order.reserve_price or fill.price, remaining, reserve_rates)
        ledger.post(session, acct, "RELEASE", new_reserved - order.reserved_amount, "order", ref)
        order.reserved_amount = new_reserved
        old_qty, new_qty = pos.qty, pos.qty + fill.qty
        # 평단 = (기존수량 x 기존평단 + 체결수량 x 체결가) / 합계, 소수 둘째 자리 반올림.
        # 수수료는 평단에 넣지 않는다 (SPEC 7.7)
        pos.avg_price = ((Decimal(old_qty) * pos.avg_price + Decimal(amount)) / Decimal(new_qty)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        pos.qty = new_qty
    else:
        fee, tax = sell_costs(amount, rates)
        ledger.post(session, acct, "SELL", amount, "order", ref)
        ledger.post(session, acct, "FEE", -fee, "order", ref)
        ledger.post(session, acct, "TAX", -tax, "order", ref)
        pnl = (Decimal(fill.price) - pos.avg_price) * fill.qty - fee - tax  # 매수수수료는 넣지 않음 (v1)
        pos.realized_pnl += int(pnl.to_integral_value(rounding=ROUND_HALF_UP))
        pos.qty -= fill.qty
        pos.reserved_qty -= fill.qty  # 평단 유지. 수량 0이 돼도 행은 남긴다 (실현손익 보존)
    trade = Trade(
        order_id=order.id,
        account_id=acct.id,
        code=order.code,
        side=order.side,
        qty=fill.qty,
        price=fill.price,
        amount=amount,
        fee=fee,
        tax=tax,
        event_seq=seq * LEVEL_SLOTS + fill.level,
        filled_at=now,
    )
    session.add(trade)
    session.flush()
    order.filled_qty += fill.qty
    events.trade(trade)


def _finish(session: Session, acct: PaperAccount, order: Order, status: str, now: datetime) -> None:
    """종료 처리: 남은 예약금/예약수량 해제."""
    if order.side == "BUY" and order.reserved_amount:
        ledger.post(session, acct, "RELEASE", -order.reserved_amount, "order", _ref(order))
        order.reserved_amount = 0
    if order.side == "SELL":
        left = order.qty - order.filled_qty
        if left:
            pos = _position(session, acct.id, order.code)
            pos.reserved_qty -= left
    order.status = status
    order.updated_at = now


def _try_fill(
    session: Session, acct: PaperAccount, order: Order, mkt: MarketSnapshot, now: datetime, events: Events
) -> bool:
    """체결을 시도한다. 바뀐 게 있으면 True."""
    if order.status in TERMINAL:
        return False
    if not settings.paper_ignore_market_hours and is_expired(session, order, now):
        _finish(session, acct, order, "EXPIRED", now)  # 지난 장의 DAY 주문은 체결하지 않고 만료
        return True
    seq = mkt.book.seq if mkt.book else 0
    if mkt.book and _already_processed(session, order, seq):
        return False  # 같은 호가 이벤트는 한 번만 (멱등)
    remaining = order.qty - order.filled_qty
    fills = match(order.side, order.order_type, remaining, order.limit_price, mkt.book, mkt.last_price)
    if not fills:
        if order.queued:
            order.queued = False
            order.updated_at = now
            return True
        return False
    if not mkt.book and _already_processed(session, order, seq):
        return False
    order.queued = False
    for f in fills:
        _apply_fill(session, acct, order, f, seq, now, events)
    if order.filled_qty >= order.qty:
        _finish(session, acct, order, "FILLED", now)
    else:
        order.status = "PARTIAL"
        order.updated_at = now
    return True


def process_market_event(
    session: Session, code: str, mkt: MarketSnapshot, now: datetime, events: Events
) -> list[Order]:
    """호가/체결 이벤트 1건에 대해 해당 종목의 미체결 주문을 판정한다 (장 시간에만).
    장 외 접수(queued) 주문도 장이 열린 뒤 첫 이벤트에서 처리된다 (SPEC 7.1)."""
    if not is_market_open(session, now):
        return []
    rows = session.execute(
        select(Order.id, Order.account_id)
        .where(and_(Order.code == code, Order.status.in_(ACTIVE)))
        .order_by(Order.account_id, Order.created_at)
    ).all()
    changed: list[Order] = []
    for account_id in sorted({r.account_id for r in rows}):
        try:
            acct = lock_account(session, account_id)
        except E.PaperError:
            continue
        for r in rows:
            if r.account_id != account_id:
                continue
            order = _lock_order(session, r.id)
            if order is None or order.status in TERMINAL:
                continue
            if _try_fill(session, acct, order, mkt, now, events):
                events.order(order)
                changed.append(order)
    return changed


# ---------------------------------------------------------------- 취소 / 만료
def cancel_order(session: Session, account_id: int, order_id: UUID, now: datetime, events: Events) -> Order:
    """PENDING/PARTIAL 만 취소. 체결분은 유지하고 미체결분의 예약만 해제한다 (FR-PT-4)."""
    acct = lock_account(session, account_id)
    order = _lock_order(session, order_id)
    if order is None or order.account_id != acct.id:
        raise E.PaperError(E.ORDER_NOT_CANCELABLE, 404, "주문이 없습니다.")
    if order.status not in ACTIVE:
        raise E.PaperError(E.ORDER_NOT_CANCELABLE, 409)
    _finish(session, acct, order, "CANCELED", now)
    events.order(order)
    return order


def expire_day_orders(session: Session, now: datetime, events: Events) -> int:
    """장 마감(15:30) 처리: 속한 장이 끝난 미체결 DAY 주문을 EXPIRED 로 바꾸고 예약을 해제한다.
    (SPEC 7.1)
    장 마감 후 접수한 주문은 다음 장 소속이라 건드리지 않는다.
    늦게 실행돼도 밀린 주문까지 정리한다(catch-up)."""
    rows = session.execute(
        select(Order.id, Order.account_id).where(Order.status.in_(ACTIVE)).order_by(Order.account_id)
    ).all()
    n = 0
    for account_id in sorted({r.account_id for r in rows}):
        try:
            acct = lock_account(session, account_id)
        except E.PaperError:
            continue
        for r in (x for x in rows if x.account_id == account_id):
            order = _lock_order(session, r.id)
            if order is None or order.status not in ACTIVE or not is_expired(session, order, now):
                continue
            _finish(session, acct, order, "EXPIRED", now)
            events.order(order)
            n += 1
    return n


def cancel_all_active(session: Session, acct: PaperAccount, now: datetime, events: Events) -> int:
    """계좌의 모든 미체결 주문 취소 (계좌 초기화용). acct 는 이미 잠겨 있어야 한다."""
    ids: Sequence[UUID] = session.scalars(
        select(Order.id).where(Order.account_id == acct.id, Order.status.in_(ACTIVE))
    ).all()
    for oid in ids:
        order = _lock_order(session, oid)
        if order is not None and order.status in ACTIVE:
            _finish(session, acct, order, "CANCELED", now)
            events.order(order)
    return len(ids)
