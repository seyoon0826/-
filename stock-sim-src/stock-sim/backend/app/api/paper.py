"""모의투자 API (SPEC 10.4, 로그인 필수). 엔진 호출은 스레드풀에서 하나의 트랜잭션으로 실행한다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import ApiError, envelope, get_store
from app.api.deps import current_user_id, optional_user_id
from app.core.db import get_db
from app.models import Order, PaperAccount, Position, Prediction, Trade
from app.services.market_data import KST
from app.services.market_store import QuoteStore
from app.services.paper import accounts as acc
from app.services.paper import engine
from app.services.paper.engine import Events, OrderRequest, order_dict, trade_dict
from app.services.paper.errors import ACCOUNT_NOT_FOUND, PaperError
from app.services.paper.events import publish_async
from app.services.paper.fees import buy_fee, buy_reserve, rates_on, sell_costs
from app.services.paper.market import build_snapshot, last_close
from app.services.paper.matching import match

router = APIRouter()


def _now() -> datetime:
    return datetime.now(UTC)


def _raise(e: PaperError) -> None:
    raise ApiError(e.status, e.code, e.message)


def _user_account(db: Session, user_id: int, kind: str = "USER") -> PaperAccount:
    user, ai = acc.open_accounts(db, user_id)
    a = user if kind == "USER" else ai
    if a is None:
        raise ApiError(404, ACCOUNT_NOT_FOUND, "계좌가 없습니다. 먼저 초기 자금을 골라 계좌를 만드세요.")
    return a


async def _prices(db: Session, store: QuoteStore, codes: list[str]) -> dict[str, int]:
    """평가용 현재가: Redis 실시간 우선, 없으면 DB 최근 종가."""
    out: dict[str, int] = {}
    for code in codes:
        q = await store.get_quote(code)
        price = q.price if q is not None else await run_in_threadpool(last_close, db, code)
        if price:
            out[code] = price
    return out


async def _held_codes(db: Session, account_id: int) -> list[str]:
    def q() -> list[str]:
        return list(
            db.scalars(select(Position.code).where(Position.account_id == account_id, Position.qty > 0))
        )

    return await run_in_threadpool(q)


# ---------------------------------------------------------------- 계좌
class AccountCreate(BaseModel):
    initial_cash: int


class AccountReset(BaseModel):
    initial_cash: int | None = None


async def _summary(db: Session, store: QuoteStore, a: PaperAccount) -> dict:
    prices = await _prices(db, store, await _held_codes(db, a.id))
    return await run_in_threadpool(acc.portfolio, db, a, prices)


@router.post("/accounts", status_code=201)
async def create_account(
    body: AccountCreate,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
    store: QuoteStore = Depends(get_store),
):
    def work() -> tuple[PaperAccount, PaperAccount]:
        try:
            pair = acc.create_accounts(db, user_id, body.initial_cash, _now())
            db.commit()
            return pair
        except PaperError as e:
            db.rollback()
            _raise(e)
            raise

    user, ai = await run_in_threadpool(work)
    return envelope({"user": await _summary(db, store, user), "ai": await _summary(db, store, ai)})


@router.get("/accounts/me")
async def my_accounts(
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
    store: QuoteStore = Depends(get_store),
):
    user = _user_account(db, user_id)
    ai = _user_account(db, user_id, "AI")
    return envelope({"user": await _summary(db, store, user), "ai": await _summary(db, store, ai)})


@router.post("/accounts/reset")
async def reset_account(
    body: AccountReset,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
    store: QuoteStore = Depends(get_store),
):
    events = Events()

    def work() -> tuple[PaperAccount, PaperAccount]:
        try:
            pair = acc.reset_accounts(db, user_id, body.initial_cash, _now(), events)
            db.commit()
            return pair
        except PaperError as e:
            db.rollback()
            _raise(e)
            raise

    user, ai = await run_in_threadpool(work)
    await publish_async(store.r, events)
    return envelope({"user": await _summary(db, store, user), "ai": await _summary(db, store, ai)})


# ---------------------------------------------------------------- 주문
class OrderCreate(BaseModel):
    code: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    qty: float  # 정수가 아닌 값도 받아서 INVALID_QTY 로 거부·기록한다 (SPEC 7.2)
    limit_price: int | None = None


def _qty(v: float) -> int:
    return int(v) if float(v).is_integer() else -1


@router.post("/orders", status_code=201)
async def create_order(
    body: OrderCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
    store: QuoteStore = Depends(get_store),
):
    quote, book = await store.get_quote(body.code), await store.get_orderbook(body.code)
    events = Events()
    req = OrderRequest(
        body.code, body.side, body.order_type, _qty(body.qty), body.limit_price, idempotency_key
    )

    def work() -> tuple[dict, list[dict]]:
        account = _user_account(db, user_id)
        now = _now()
        try:
            mkt = build_snapshot(db, body.code, quote, book, now)
            order = engine.place_order(db, account.id, req, mkt, now, events)
            db.commit()
        except PaperError as e:
            db.rollback()
            _raise(e)
            raise
        except IntegrityError:  # 같은 Idempotency-Key 동시 요청: 먼저 들어간 주문을 돌려준다
            db.rollback()
            order = db.scalars(
                select(Order).where(Order.account_id == account.id, Order.idempotency_key == idempotency_key)
            ).one()
        trades = [
            trade_dict(t)
            for t in db.scalars(select(Trade).where(Trade.order_id == order.id).order_by(Trade.id))
        ]
        return order_dict(order), trades

    order, trades = await run_in_threadpool(work)
    await publish_async(store.r, events)
    if order["status"] == "REJECTED":
        return JSONResponse(
            status_code=422,
            content={
                "error": {"code": order["reject_reason"], "message": _reject_message(order["reject_reason"])},
                "data": {**order, "trades": []},
            },
        )
    return envelope({**order, "trades": trades})


def _reject_message(code: str) -> str:
    from app.services.paper.errors import MESSAGES

    return MESSAGES.get(code, code)


@router.delete("/orders/{order_id}")
async def cancel_order(
    order_id: UUID,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
    store: QuoteStore = Depends(get_store),
):
    events = Events()

    def work() -> dict:
        account = _user_account(db, user_id)
        try:
            order = engine.cancel_order(db, account.id, order_id, _now(), events)
            db.commit()
            return order_dict(order)
        except PaperError as e:
            db.rollback()
            _raise(e)
            raise

    order = await run_in_threadpool(work)
    await publish_async(store.r, events)
    return envelope(order)


@router.get("/orders")
async def list_orders(
    status: str | None = None,
    code: str | None = None,
    account: Literal["me", "ai"] = "me",  # ai: 내 AI 계좌의 주문 (근거 prediction_id 포함, FR-AI-4)
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    def work() -> tuple[list[dict], str | None]:
        account_ = _user_account(db, user_id, "USER" if account == "me" else "AI")
        q = select(Order).where(Order.account_id == account_.id)
        if status:
            q = q.where(Order.status.in_(status.split(",")))
        if code:
            q = q.where(Order.code == code)
        if cursor:
            q = q.where(Order.created_at < datetime.fromisoformat(cursor))
        rows = list(db.scalars(q.order_by(Order.created_at.desc()).limit(limit + 1)))
        nxt = rows[limit - 1].created_at.isoformat() if len(rows) > limit else None
        rows = rows[:limit]
        pids = {o.prediction_id for o in rows if o.prediction_id}
        preds = (
            {p.id: p for p in db.scalars(select(Prediction).where(Prediction.id.in_(pids)))} if pids else {}
        )

        def with_basis(o: Order) -> dict:
            d = order_dict(o)
            p = preds.get(o.prediction_id) if o.prediction_id else None
            d["basis"] = {"p_up": float(p.p_up), "as_of_date": p.as_of_date.isoformat()} if p else None
            return d

        return [with_basis(o) for o in rows], nxt

    items, nxt = await run_in_threadpool(work)
    return envelope(items, {"next_cursor": nxt})


@router.get("/trades")
async def list_trades(
    code: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: int | None = None,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    def work() -> tuple[list[dict], int | None]:
        account = _user_account(db, user_id)
        q = select(Trade).where(Trade.account_id == account.id)
        if code:
            q = q.where(Trade.code == code)
        if cursor:
            q = q.where(Trade.id < cursor)
        rows = list(db.scalars(q.order_by(Trade.id.desc()).limit(limit + 1)))
        nxt = rows[limit - 1].id if len(rows) > limit else None
        return [trade_dict(t) for t in rows[:limit]], nxt

    items, nxt = await run_in_threadpool(work)
    return envelope(items, {"next_cursor": nxt})


# ---------------------------------------------------------------- 평가/성과
@router.get("/portfolio")
async def get_portfolio(
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
    store: QuoteStore = Depends(get_store),
):
    return envelope(await _summary(db, store, _user_account(db, user_id)))


@router.get("/performance")
async def get_performance(
    account: Literal["me", "ai"] = "me",
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    a = _user_account(db, user_id, "USER" if account == "me" else "AI")
    return envelope(await run_in_threadpool(acc.performance, db, a))


@router.get("/leaderboard")
async def get_leaderboard(
    limit: int = Query(50, ge=1, le=200),
    viewer_id: int | None = Depends(optional_user_id),
    db: Session = Depends(get_db),
):
    """모의투자 수익률 랭킹 (로그인 없이 볼 수 있다. 이름은 가린다). 평가는 최근 종가 기준."""

    def work() -> dict:
        codes = list(db.scalars(select(Position.code).where(Position.qty > 0).distinct()))
        prices = {c: p for c in codes if (p := last_close(db, c))}
        return acc.leaderboard(db, prices, viewer_id, limit)

    data = await run_in_threadpool(work)
    return envelope(data, {"valuation": "최근 종가", "eligibility": "체결 1회 이상인 계좌"})


@router.get("/estimate")
async def estimate(
    code: str,
    side: Literal["BUY", "SELL"],
    order_type: Literal["MARKET", "LIMIT"],
    qty: int = Query(ge=1),
    limit_price: int | None = None,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
    store: QuoteStore = Depends(get_store),
):
    """예상 체결금액·수수료·세금 (DB 를 바꾸지 않는다). 지금 호가로 즉시 체결될 부분은 호가 기준,
    나머지(지정가 대기분)는 지정가 기준으로 계산한다."""
    quote, book = await store.get_quote(code), await store.get_orderbook(code)

    def work() -> dict:
        now = _now()
        mkt = build_snapshot(db, code, quote, book, now)
        rates = rates_on(db, now.astimezone(KST).date())
        fills = match(side, order_type, qty, limit_price, mkt.book, mkt.last_price)
        filled = sum(f.qty for f in fills)
        amount = sum(f.price * f.qty for f in fills)
        if order_type == "LIMIT" and limit_price and filled < qty:
            amount += limit_price * (qty - filled)
        if side == "BUY":
            fee, tax = buy_fee(amount, rates), 0
            total = amount + fee
        else:
            fee, tax = sell_costs(amount, rates)
            total = amount - fee - tax
        reserve = None
        if side == "BUY":
            from app.services.ticks import price_limits

            basis = (
                limit_price
                if order_type == "LIMIT"
                else (price_limits(mkt.prev_close)[1] if mkt.prev_close else None)
            )
            reserve = buy_reserve(basis, qty, rates) if basis else None
        return {
            "code": code,
            "qty": qty,
            "immediately_fillable_qty": filled,
            "avg_price": round(amount / qty, 2) if qty else None,
            "amount": amount,
            "fee": fee,
            "tax": tax,
            "total": total,  # 매수: 필요 금액, 매도: 받을 금액
            "required_reserve": reserve,
            "rates": {
                "buy_fee": str(rates.buy_fee),
                "sell_fee": str(rates.sell_fee),
                "sell_tax": str(rates.sell_tax),
            },
        }

    return envelope(await run_in_threadpool(work))
