"""모의투자 테스트 공용 도구. 실제 요율(마이그레이션 0002 와 같은 값)을 쓴다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import FeeConfig, Order, PaperAccount, Position, Stock, Trade, User
from app.services.market_data import KST, BookLevel, OrderBook
from app.services.paper import accounts as acc
from app.services.paper import engine
from app.services.paper.engine import Events, MarketSnapshot, OrderRequest
from app.services.paper.ledger import invariant_violations

OPEN = datetime(2026, 10, 21, 10, 0, tzinfo=KST)  # 수요일 장중
CLOSED = datetime(2026, 10, 21, 20, 0, tzinfo=KST)  # 장 마감 후
NEXT_OPEN = datetime(2026, 10, 22, 9, 0, 5, tzinfo=KST)
FEE = Decimal("0.000176923")
TAX = Decimal("0.002")
PREV_CLOSE = 70_000


def book(asks=(), bids=(), seq=1, ts=OPEN, source="kis") -> OrderBook:
    return OrderBook(
        "005930",
        tuple(BookLevel(p, q) for p, q in asks),
        tuple(BookLevel(p, q) for p, q in bids),
        ts,
        source,
        seq,
    )


def mkt(
    b: OrderBook | None = None, last=None, ts=OPEN, source="kis", prev_close=PREV_CLOSE
) -> MarketSnapshot:
    return MarketSnapshot(b, last, ts, source, prev_close)


@dataclass
class Paper:
    sf: object
    user_id: int
    account_id: int
    ai_account_id: int

    def place(
        self, req: OrderRequest, m: MarketSnapshot, now: datetime = OPEN, account_id: int | None = None
    ):
        with self.sf() as s:
            o = engine.place_order(s, account_id or self.account_id, req, m, now, Events())
            s.commit()
            return o.id

    def event(self, m: MarketSnapshot, now: datetime = OPEN, code: str = "005930") -> int:
        with self.sf() as s:
            n = len(engine.process_market_event(s, code, m, now, Events()))
            s.commit()
            return n

    def cancel(self, order_id, now: datetime = OPEN):
        with self.sf() as s:
            o = engine.cancel_order(s, self.account_id, order_id, now, Events())
            s.commit()
            return o.status

    def expire(self, now: datetime) -> int:
        with self.sf() as s:
            n = engine.expire_day_orders(s, now, Events())
            s.commit()
            return n

    def order(self, order_id) -> Order:
        with self.sf() as s:
            return s.get(Order, order_id)

    def account(self, account_id: int | None = None) -> PaperAccount:
        with self.sf() as s:
            return s.get(PaperAccount, account_id or self.account_id)

    def position(self, code: str = "005930", account_id: int | None = None) -> Position | None:
        with self.sf() as s:
            return s.get(Position, (account_id or self.account_id, code))

    def trades(self, order_id=None) -> list[Trade]:
        with self.sf() as s:
            q = select(Trade).order_by(Trade.id)
            if order_id is not None:
                q = q.where(Trade.order_id == order_id)
            return list(s.scalars(q))

    def violations(self) -> dict[int, list[str]]:
        with self.sf() as s:
            ids = list(s.scalars(select(PaperAccount.id)))
            return {i: v for i in ids if (v := invariant_violations(s, i))}


def setup(sf, initial_cash: int = 10_000_000, codes=("005930", "000660")) -> Paper:
    with sf() as s:
        # 마이그레이션 0002 가 넣은 실제 요율을 그대로 쓴다 (없을 때만 같은 값으로 보충)
        if s.scalars(select(FeeConfig).where(FeeConfig.effective_from == date(2026, 1, 1))).first() is None:
            s.add(
                FeeConfig(
                    effective_from=date(2026, 1, 1), buy_fee_rate=FEE, sell_fee_rate=FEE, sell_tax_rate=TAX
                )
            )
        for c in codes:
            s.add(Stock(code=c, name=f"종목{c}"))
        s.add(Stock(code="999999", name="편출", active=False))
        user = User(email="u@example.com", password_hash="x")
        s.add(user)
        s.flush()
        a, ai = acc.create_accounts(s, user.id, initial_cash, OPEN - timedelta(days=1))
        s.commit()
        return Paper(sf, user.id, a.id, ai.id)


def buy(qty, price=None, **kw) -> OrderRequest:
    return OrderRequest("005930", "BUY", "LIMIT" if price else "MARKET", qty, price, **kw)


def sell(qty, price=None, **kw) -> OrderRequest:
    return OrderRequest("005930", "SELL", "LIMIT" if price else "MARKET", qty, price, **kw)
