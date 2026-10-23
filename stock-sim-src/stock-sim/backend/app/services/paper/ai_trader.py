"""AI 계정 자동매매 (FR-AI-2/3/4). 반드시 일반 주문과 같은 engine.place_order 를 통과한다 — 특별 경로 없음.

규칙 (설정값 AI_TOP_N / AI_BUY_THRESHOLD / AI_SELL_THRESHOLD):
- 신호: 직전 거래일 장 마감 후 배치로 만든 예측 (오늘 장 전에 확정된 정보만 쓴다)
- 보유 중인데 P(up) < 매도 기준(0.50) -> 전량 시장가 매도
- P(up) 상위 N(5) 종목 중 P(up) >= 매수 기준(0.55) 인 종목: 총자산/N 을 목표 금액으로 모자란 만큼 시장가 매수
  (조건을 만족하는 종목이 N 보다 적으면 나머지는 현금으로 둔다. 목표보다 많이 든 종목은 줄이지 않는다)
- 순서: 매도 먼저, 그다음 매수를 한 종목씩. 시장가 매수는 상한가 기준으로 예약했다가 체결 후
  차액을 풀기 때문에 앞 주문이 체결돼야 다음 주문에 돈이 생긴다. 예약금이 모자라면 살 수 있는 만큼으로 줄인다.
- 주문마다 근거 예측 ID 를 prediction_id 로 남긴다 (FR-AI-4).
- Idempotency-Key = ai:{신호 기준일}:{종목}:{매수/매도} -> 같은 날 다시 돌려도 주문이 두 번 나가지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Order, PaperAccount, Position, Prediction
from app.services.market_data import KST
from app.services.paper.engine import Events, MarketSnapshot, OrderRequest, is_trading_day, place_order
from app.services.paper.fees import buy_reserve, rates_on
from app.services.ticks import price_limits


@dataclass(frozen=True)
class Rules:
    top_n: int
    buy_threshold: float
    sell_threshold: float

    @classmethod
    def from_settings(cls) -> Rules:
        return cls(settings.ai_top_n, settings.ai_buy_threshold, settings.ai_sell_threshold)


@dataclass(frozen=True)
class Signal:
    code: str
    p_up: float
    prediction_id: int


@dataclass(frozen=True)
class Planned:
    code: str
    side: str  # BUY | SELL
    qty: int
    prediction_id: int
    reason: str


def previous_trading_day(session: Session, today: date) -> date:
    d = today - timedelta(days=1)
    for _ in range(30):
        if is_trading_day(session, d):
            return d
        d -= timedelta(days=1)
    return d


def signals(session: Session, as_of: date, version: str | None) -> dict[str, Signal]:
    """기준일 as_of 의 예측 (서빙 중인 모델 버전)."""
    q = select(Prediction).where(Prediction.as_of_date == as_of)
    if version:
        q = q.where(Prediction.model_version == version)
    return {p.code: Signal(p.code, float(p.p_up), p.id) for p in session.scalars(q)}


def plan(
    holdings: dict[str, int], prices: dict[str, int], total_value: int, sig: dict[str, Signal], rules: Rules
) -> list[Planned]:
    """holdings: 종목 -> 팔 수 있는 수량. 반환은 매도 먼저, 매수는 확률 높은 순."""
    out: list[Planned] = []
    for code, qty in sorted(holdings.items()):
        s = sig.get(code)
        if qty > 0 and s is not None and s.p_up < rules.sell_threshold:
            out.append(
                Planned(code, "SELL", qty, s.prediction_id, f"P(up) {s.p_up:.2f} < {rules.sell_threshold}")
            )
    sold = {p.code for p in out}
    ranked = sorted(sig.values(), key=lambda s: (-s.p_up, s.code))[: rules.top_n]
    target = total_value // rules.top_n
    for s in ranked:
        price = prices.get(s.code)
        if s.p_up < rules.buy_threshold or not price or s.code in sold:
            continue
        have = holdings.get(s.code, 0) * price
        qty = (target - have) // price
        if qty > 0:
            out.append(
                Planned(
                    s.code,
                    "BUY",
                    qty,
                    s.prediction_id,
                    f"P(up) {s.p_up:.2f} 상위 {rules.top_n} · 목표 {target:,}원",
                )
            )
    return out


def affordable_qty(cash_available: int, prev_close: int | None, qty: int, session: Session, day: date) -> int:
    """시장가 매수 예약(상한가 x 수량 x (1+수수료))이 가용 현금 안에 들어오는 최대 수량."""
    if not prev_close:
        return 0
    upper = price_limits(prev_close)[1]
    rates = rates_on(session, day)
    q = min(qty, int(Decimal(cash_available) / (Decimal(upper) * (1 + rates.buy_fee))))
    while q > 0 and buy_reserve(upper, q, rates) > cash_available:
        q -= 1
    return max(q, 0)


SnapshotFn = Callable[[Session, str, datetime], MarketSnapshot]


def rebalance_account(
    session_factory: Callable[[], Session],
    account_id: int,
    snapshot: SnapshotFn,
    now: datetime,
    rules: Rules | None = None,
    version: str | None = None,
) -> tuple[list[dict[str, object]], Events]:
    """AI 계좌 하나를 리밸런싱한다. 주문마다 별도 트랜잭션(일반 주문 API 와 같은 단위)."""
    rules = rules or Rules.from_settings()
    version = version if version is not None else (settings.model_version or None)
    events = Events()
    today = now.astimezone(KST).date()
    with session_factory() as s:
        as_of = previous_trading_day(s, today)
        sig = signals(s, as_of, version)
        acct = s.get(PaperAccount, account_id)
        if acct is None or acct.closed_at is not None or not sig:
            return [], events
        held = list(s.scalars(select(Position).where(Position.account_id == account_id, Position.qty > 0)))
        pos = {p.code: p.qty - p.reserved_qty for p in held}
        prices: dict[str, int] = {}
        for c in sorted(set(pos) | set(sig)):
            m = snapshot(s, c, now)
            px = m.last_price or m.prev_close
            if px:
                prices[c] = px
        # 총자산 = 현금 + 보유 평가액 (시세가 없으면 평단으로 평가)
        total = acct.cash + sum(p.qty * prices.get(p.code, int(p.avg_price)) for p in held)
        planned = plan(pos, prices, total, sig, rules)

    results: list[dict[str, object]] = []
    for p in planned:
        key = f"ai:{as_of.isoformat()}:{p.code}:{p.side}"
        with session_factory() as s:
            if s.scalar(
                select(func.count())
                .select_from(Order)
                .where(Order.account_id == account_id, Order.idempotency_key == key)
            ):
                continue  # 오늘 이미 낸 주문 (재실행)
            mkt = snapshot(s, p.code, now)
            qty = p.qty
            if p.side == "BUY":
                acct = s.get(PaperAccount, account_id)
                assert acct is not None
                qty = affordable_qty(acct.cash - acct.reserved_cash, mkt.prev_close, qty, s, today)
                if qty <= 0:
                    continue
            req = OrderRequest(
                p.code, p.side, "MARKET", qty, None, key, source="AI", prediction_id=p.prediction_id
            )
            order = place_order(s, account_id, req, mkt, now, events)
            s.commit()
            results.append(
                {"code": p.code, "side": p.side, "qty": qty, "status": order.status, "reason": p.reason}
            )
    return results, events


def ai_accounts(session: Session) -> list[int]:
    return list(
        session.scalars(
            select(PaperAccount.id).where(PaperAccount.type == "AI", PaperAccount.closed_at.is_(None))
        )
    )
