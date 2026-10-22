"""모의투자 엔진 (SPEC 7, 16.1 필수 테스트). 실제 DB, 실제 요율(수수료 0.0176923%, 거래세 0.20%)."""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.paper import errors as E
from app.services.paper.engine import Events, OrderRequest
from app.services.paper.fees import FeeRates, buy_fee, buy_reserve, sell_costs
from tests.paper_support import CLOSED, FEE, NEXT_OPEN, OPEN, TAX, book, buy, mkt, sell, setup

RATES = FeeRates(FEE, FEE, TAX)


@pytest.fixture
def paper(sf):
    p = setup(sf)
    yield p
    # 필수 11: 모든 테스트 끝에 원장/예약 불변식 검사 (SPEC 7.8)
    assert p.violations() == {}


def asks(*levels, seq=1):
    return mkt(book(asks=levels, seq=seq))


def bids(*levels, seq=1):
    return mkt(book(bids=levels, seq=seq))


def holding(paper, qty=10, price=70_000):
    """테스트용 보유 주식 만들기 (일반 주문 경로로 매수)."""
    oid = paper.place(buy(qty, price), asks((price, qty), seq=900))
    assert paper.order(oid).status == "FILLED"


# ---------------------------------------------------------------- 비용 (SPEC 7.5)
def test_costs_are_floored_and_reserve_is_ceiled():
    assert buy_fee(700_000, RATES) == 123  # 123.85 -> 123
    assert sell_costs(720_000, RATES) == (127, 1_440)  # 127.38 -> 127, 1,440
    assert buy_reserve(70_000, 10, RATES) == 700_124  # 700,123.85 -> 700,124
    assert buy_reserve(70_000, 0, RATES) == 0


# ---------------------------------------------------------------- 필수 1: 평단가
def test_1_avg_price_10_at_70000_plus_10_at_72000_is_71000(paper):
    paper.place(buy(10, 70_000), asks((70_000, 10)))
    paper.place(buy(10, 72_000), asks((72_000, 10), seq=2))
    pos = paper.position()
    assert pos.qty == 20 and pos.avg_price == Decimal("71000.00")


def test_avg_price_rounds_to_two_decimals(paper):
    paper.place(buy(3, 70_000), asks((70_000, 3)))
    paper.place(buy(4, 70_100), asks((70_100, 4), seq=2))
    assert paper.position().avg_price == Decimal("70057.14")  # 490,400 / 7 = 70,057.142...


# ---------------------------------------------------------------- 필수 2: 매도 실현손익
def test_2_sell_realized_pnl_includes_fee_and_tax(paper):
    paper.place(buy(10, 70_000), asks((70_000, 10)))
    oid = paper.place(sell(10, 72_000), bids((72_000, 10), seq=2))
    [t] = paper.trades(oid)
    assert (t.amount, t.fee, t.tax) == (720_000, 127, 1_440)
    pos = paper.position()
    # (72,000 - 70,000) x 10 - 127 - 1,440 = 18,433 (매수 수수료는 넣지 않음, v1)
    assert pos.realized_pnl == 18_433 and pos.qty == 0 and pos.reserved_qty == 0
    assert pos.avg_price == Decimal("70000.00")  # 매도는 평단 유지, 수량 0 행도 보존
    # 현금: 1,000만 - 700,000 - 123 + 720,000 - 127 - 1,440
    assert paper.account().cash == 10_018_310


# ---------------------------------------------------------------- 필수 3: 시장가 다단 체결
def test_3_market_buy_walks_three_levels(paper):
    oid = paper.place(buy(12), asks((70_000, 5), (70_100, 5), (70_200, 10)))
    o = paper.order(oid)
    ts = paper.trades(oid)
    assert o.status == "FILLED" and len(ts) == 3
    assert [(t.price, t.qty) for t in ts] == [(70_000, 5), (70_100, 5), (70_200, 2)]
    assert paper.position().avg_price == Decimal("70075.00")  # 840,900 / 12
    assert paper.account().reserved_cash == 0  # 상한가로 잡은 예약은 체결 후 전부 해제


def test_market_buy_beyond_10_levels_fills_rest_at_last_level(paper):
    levels = [(70_000 + 100 * i, 1) for i in range(10)]
    oid = paper.place(buy(15), asks(*levels))
    ts = paper.trades(oid)
    assert sum(t.qty for t in ts) == 15
    assert ts[-1].price == 70_900 and ts[-1].qty == 6  # 10호가 가격으로 남은 수량 (v1 단순화)


def test_market_sell_walks_bids_downward(paper):
    holding(paper, 10)
    oid = paper.place(sell(10), bids((69_900, 4), (69_800, 10), seq=2))
    assert [(t.price, t.qty) for t in paper.trades(oid)] == [(69_900, 4), (69_800, 6)]


def test_market_buy_reserves_at_upper_limit(paper):
    oid = paper.place(buy(10), mkt(None, last=None), now=CLOSED)  # 장 외: 예약만 하고 대기
    o = paper.order(oid)
    assert o.queued and o.reserve_price == 91_000  # 상한가 = 70,000 x 1.3
    assert o.reserved_amount == buy_reserve(91_000, 10, RATES) == paper.account().reserved_cash


# ---------------------------------------------------------------- 필수 4: 지정가 대기 -> 부분 -> 전량
def test_4_limit_buy_waits_then_partial_then_filled(paper):
    oid = paper.place(buy(10, 70_000), asks((70_100, 100)))
    o = paper.order(oid)
    assert o.status == "PENDING" and o.filled_qty == 0 and o.reserved_amount == 700_124

    paper.event(asks((70_000, 4), (70_100, 50), seq=2))
    o = paper.order(oid)
    assert o.status == "PARTIAL" and o.filled_qty == 4
    assert o.reserved_amount == buy_reserve(70_000, 6, RATES)  # 남은 6주분만 예약 유지

    paper.event(asks((69_900, 10), seq=3))
    o = paper.order(oid)
    assert o.status == "FILLED" and o.filled_qty == 10 and o.reserved_amount == 0
    assert [(t.price, t.qty) for t in paper.trades(oid)] == [
        (70_000, 4),
        (69_900, 6),
    ]  # 지정가보다 유리한 체결
    assert paper.account().reserved_cash == 0


def test_limit_sell_waits_until_bid_reaches_limit(paper):
    holding(paper, 10)
    oid = paper.place(sell(10, 71_000), bids((70_900, 100), seq=2))
    assert paper.order(oid).status == "PENDING" and paper.position().reserved_qty == 10
    paper.event(bids((71_100, 3), (71_000, 3), (70_900, 100), seq=3))
    o = paper.order(oid)
    assert o.status == "PARTIAL" and o.filled_qty == 6
    assert paper.position().qty == 4 and paper.position().reserved_qty == 4


# ---------------------------------------------------------------- 필수 5: 부분체결 후 취소
def test_5_cancel_partial_keeps_fills_and_releases_rest(paper):
    oid = paper.place(buy(10, 70_000), asks((70_000, 4)))
    assert paper.order(oid).status == "PARTIAL"
    cash_after_fill = paper.account().cash
    assert paper.cancel(oid) == "CANCELED"
    o = paper.order(oid)
    assert o.filled_qty == 4 and o.reserved_amount == 0
    assert paper.position().qty == 4  # 체결분 유지
    a = paper.account()
    assert a.reserved_cash == 0 and a.cash == cash_after_fill  # 해제는 cash 를 바꾸지 않는다


def test_cancel_sell_releases_reserved_qty(paper):
    holding(paper, 10)
    oid = paper.place(sell(10, 75_000), bids((70_000, 10), seq=2))
    paper.cancel(oid)
    assert paper.position().reserved_qty == 0


# ---------------------------------------------------------------- 필수 6: 장 마감 만료
def test_6_close_expires_day_orders_and_releases(paper):
    holding(paper, 10)
    b = paper.place(buy(5, 69_000), asks((70_000, 10), seq=2))
    s_ = paper.place(sell(5, 75_000), bids((70_000, 10), seq=3))
    assert paper.expire(CLOSED) == 2
    assert paper.order(b).status == paper.order(s_).status == "EXPIRED"
    assert paper.account().reserved_cash == 0 and paper.position().reserved_qty == 0


def test_expire_leaves_after_hours_queued_orders_for_next_session(paper):
    oid = paper.place(buy(5, 69_000), mkt(None), now=CLOSED)
    assert paper.order(oid).queued
    assert paper.expire(CLOSED + timedelta(minutes=1)) == 0
    assert paper.order(oid).status == "PENDING"


# ---------------------------------------------------------------- 필수 7: 검증 실패 (SPEC 7.2)
@pytest.mark.parametrize(
    "req,code",
    [
        (OrderRequest("123456", "BUY", "LIMIT", 1, 70_000), E.UNKNOWN_SYMBOL),
        (OrderRequest("999999", "BUY", "LIMIT", 1, 70_000), E.UNKNOWN_SYMBOL),  # 편출 종목
        (OrderRequest("005930", "BUY", "LIMIT", 0, 70_000), E.INVALID_QTY),
        (OrderRequest("005930", "BUY", "LIMIT", 100_001, 70_000), E.INVALID_QTY),  # 1회 최대 수량 초과
        (OrderRequest("005930", "BUY", "LIMIT", 1, 70_050), E.INVALID_TICK),  # 5만~20만: 100원 단위
        (OrderRequest("005930", "BUY", "LIMIT", 1, 91_100), E.PRICE_OUT_OF_LIMIT),  # 상한가 91,000 초과
        (OrderRequest("005930", "SELL", "LIMIT", 1, 48_900), E.PRICE_OUT_OF_LIMIT),  # 하한가 49,000 미만
        (OrderRequest("005930", "BUY", "LIMIT", 143, 70_000), E.INSUFFICIENT_CASH),  # 143주 x 7만 > 1,000만
        (OrderRequest("005930", "SELL", "LIMIT", 1, 70_000), E.INSUFFICIENT_QTY),
    ],
)
def test_7_rejections_have_exact_codes_and_are_saved(paper, req, code):
    oid = paper.place(req, asks((70_000, 1_000)))
    o = paper.order(oid)
    assert (o.status, o.reject_reason) == ("REJECTED", code)
    assert o.reserved_amount == 0 and paper.trades(oid) == []
    assert paper.account().reserved_cash == 0


def test_validation_order_first_failure_wins(paper):
    # 수량도 틀리고 틱도 틀리면 수량(2번) 에러가 먼저 (SPEC 7.2 순서)
    oid = paper.place(OrderRequest("005930", "BUY", "LIMIT", 0, 70_050), asks((70_000, 1)))
    assert paper.order(oid).reject_reason == E.INVALID_QTY


def test_insufficient_cash_counts_existing_reservations(paper):
    paper.place(buy(100, 70_000), asks((70_100, 1)))  # 700만 원 예약, 대기
    oid = paper.place(buy(50, 70_000), asks((70_100, 1), seq=2))  # 가용 약 300만 < 350만
    assert paper.order(oid).reject_reason == E.INSUFFICIENT_CASH


def test_sell_qty_counts_reserved_qty(paper):
    holding(paper, 10)
    paper.place(sell(8, 75_000), bids((70_000, 10), seq=2))
    oid = paper.place(sell(3, 75_000), bids((70_000, 10), seq=3))  # 10 - 8 < 3
    assert paper.order(oid).reject_reason == E.INSUFFICIENT_QTY


def test_stale_realtime_quote_rejects_market_order(paper):
    stale = mkt(book(asks=[(70_000, 10)]), last=70_000, ts=OPEN - timedelta(seconds=31))
    oid = paper.place(buy(1), stale)
    assert paper.order(oid).reject_reason == E.UPSTREAM_UNAVAILABLE
    fresh = mkt(book(asks=[(70_000, 10)], seq=2), last=70_000, ts=OPEN - timedelta(seconds=30))
    assert paper.order(paper.place(buy(1), fresh)).status == "FILLED"


def test_eod_provider_quote_is_not_treated_as_stale(paper):
    """PykrxEod: 장중에도 직전 종가 합성 호가로 즉시 판정한다 (SPEC 7.1)."""
    eod = mkt(book(asks=[(70_100, 10_000)], ts=OPEN - timedelta(hours=19), source="pykrx"), source="pykrx")
    assert paper.order(paper.place(buy(1), eod)).status == "FILLED"


# ---------------------------------------------------------------- 필수 8: 종료 상태는 바뀌지 않는다
def test_8_events_on_terminal_orders_change_nothing(paper):
    filled = paper.place(buy(5, 70_000), asks((70_000, 5)))
    canceled = paper.place(buy(5, 69_000), asks((70_000, 5), seq=2))
    paper.cancel(canceled)
    rejected = paper.place(buy(0, 70_000), asks((70_000, 5), seq=3))
    before = {o: (paper.order(o).status, paper.order(o).filled_qty) for o in (filled, canceled, rejected)}
    n_trades = len(paper.trades())
    paper.event(asks((60_000, 1_000), seq=4))  # 어떤 주문이든 체결될 만한 호가
    after = {o: (paper.order(o).status, paper.order(o).filled_qty) for o in (filled, canceled, rejected)}
    assert before == after and len(paper.trades()) == n_trades
    with pytest.raises(E.PaperError) as e:
        paper.cancel(filled)
    assert e.value.code == E.ORDER_NOT_CANCELABLE


# ---------------------------------------------------------------- 필수 9: 같은 이벤트 2회 -> 1행
def test_9_same_event_twice_creates_one_trade(paper):
    oid = paper.place(buy(10, 70_000), asks((70_100, 1)))
    ev = asks((70_000, 3), seq=7)
    paper.event(ev)
    paper.event(ev)  # 같은 호가 이벤트(seq 7) 재처리
    assert len(paper.trades(oid)) == 1 and paper.order(oid).filled_qty == 3


def test_placement_and_worker_seeing_same_event_do_not_double_fill(paper):
    ev = asks((70_000, 3), seq=8)
    oid = paper.place(buy(10, 70_000), ev)  # 접수 시점에 seq 8 로 3주 체결
    paper.event(ev)  # 워커가 같은 seq 8 이벤트를 뒤늦게 처리
    assert paper.order(oid).filled_qty == 3


def test_unique_constraint_is_last_line_of_defense(paper, sf):
    from sqlalchemy.exc import IntegrityError

    from app.models import Trade

    oid = paper.place(buy(1, 70_000), asks((70_000, 1), seq=5))
    [t] = paper.trades(oid)
    with sf() as s, pytest.raises(IntegrityError):
        s.add(
            Trade(
                order_id=oid,
                account_id=t.account_id,
                code="005930",
                side="BUY",
                qty=1,
                price=1,
                amount=1,
                fee=0,
                tax=0,
                event_seq=t.event_seq,
            )
        )
        s.commit()


# ---------------------------------------------------------------- 필수 12: AI 주문도 같은 경로
def test_12_ai_orders_use_the_same_service_function(paper):
    oid = paper.place(
        buy(10, 70_000, source="AI", prediction_id=None), asks((70_000, 10)), account_id=paper.ai_account_id
    )
    o = paper.order(oid)
    assert o.source == "AI" and o.status == "FILLED"
    [t] = paper.trades(oid)
    assert t.fee == buy_fee(700_000, RATES)  # 수수료/세금 동일
    assert paper.account(paper.ai_account_id).cash == 10_000_000 - 700_000 - 123
    assert paper.account().cash == 10_000_000  # 사용자 계좌와 분리


def test_12_no_special_path_trades_and_ledger_written_only_by_engine():
    """Trade/원장 행을 만드는 코드는 엔진(engine.py)과 원장(ledger.py)뿐이다 — AI 전용 우회 경로 금지."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    construct = re.compile(r"(?<!class )\b(Trade|CashLedger)\(")  # 클래스 정의는 제외하고 생성만 찾는다
    writers = {str(p.relative_to(app_dir)) for p in app_dir.rglob("*.py") if construct.search(p.read_text())}
    assert writers == {"services/paper/engine.py", "services/paper/ledger.py"}


# ---------------------------------------------------------------- 장 외 주문 (SPEC 7.1)
def test_after_hours_market_order_fills_on_first_quote_after_open(paper):
    oid = paper.place(buy(5), mkt(None), now=CLOSED)
    o = paper.order(oid)
    assert o.status == "PENDING" and o.queued
    assert paper.event(asks((70_000, 10), seq=2), now=CLOSED + timedelta(hours=1)) == 0  # 아직 장 외
    paper.event(
        asks(
            (70_300, 10),
            seq=3,
        ),
        now=NEXT_OPEN,
    )
    o = paper.order(oid)
    assert o.status == "FILLED" and not o.queued
    assert paper.trades(oid)[0].price == 70_300


def test_after_hours_limit_order_becomes_waiting_order_at_open(paper):
    oid = paper.place(buy(5, 69_000), mkt(None), now=CLOSED)
    paper.event(asks((70_000, 10), seq=2), now=NEXT_OPEN)
    o = paper.order(oid)
    assert o.status == "PENDING" and not o.queued


def test_ignore_market_hours_setting(paper, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "paper_ignore_market_hours", True)
    assert paper.order(paper.place(buy(1, 70_000), asks((70_000, 1)), now=CLOSED)).status == "FILLED"


def test_holiday_is_closed(paper):
    from datetime import datetime

    from app.services.market_data import KST

    xmas = datetime(2026, 12, 25, 10, 0, tzinfo=KST)  # 평일이지만 휴장
    assert paper.order(paper.place(buy(1, 70_000), asks((70_000, 1)), now=xmas)).queued


# ---------------------------------------------------------------- 호가 없음 / 멱등 키 / 기타
def test_no_orderbook_fills_one_tick_worse_unlimited(paper):
    oid = paper.place(buy(100), mkt(None, last=70_000))  # 시장가는 상한가(91,000)로 예약 -> 100주까지 가능
    [t] = paper.trades(oid)
    assert (t.price, t.qty) == (70_100, 100)  # 현재가 1틱 불리, 잔량 무제한


def test_idempotency_key_returns_existing_order(paper):
    a = paper.place(buy(1, 70_000, idempotency_key="k-1"), asks((70_000, 1)))
    b = paper.place(buy(1, 70_000, idempotency_key="k-1"), asks((70_000, 1), seq=2))
    assert a == b and len(paper.trades()) == 1


def test_orders_on_closed_account_are_refused(paper, sf):
    from datetime import datetime

    from app.models import PaperAccount

    with sf() as s:
        s.get(PaperAccount, paper.account_id).closed_at = datetime.now().astimezone()
        s.commit()
    with pytest.raises(E.PaperError) as e:
        paper.place(buy(1, 70_000), asks((70_000, 1)))
    assert e.value.code == E.ACCOUNT_NOT_FOUND


def test_ledger_rows_describe_every_cash_move(paper, sf):
    from sqlalchemy import select

    from app.models import CashLedger

    paper.place(buy(10, 70_000), asks((70_000, 10)))
    with sf() as s:
        rows = [
            (r.type, r.affects, r.amount)
            for r in s.scalars(
                select(CashLedger).where(CashLedger.account_id == paper.account_id).order_by(CashLedger.id)
            )
        ]
    assert rows == [
        ("DEPOSIT", "cash", 10_000_000),
        ("RESERVE", "reserved", 700_124),
        ("BUY", "cash", -700_000),
        ("FEE", "cash", -123),
        ("RELEASE", "reserved", -700_124),
    ]


def test_events_are_collected_for_websocket(paper, sf):
    from app.services.paper import engine

    ev = Events()
    with sf() as s:
        engine.place_order(s, paper.account_id, buy(2, 70_000), asks((70_000, 2)), OPEN, ev)
        s.commit()
    kinds = [p["type"] for _, p in ev.items]
    assert kinds == ["trade", "order_update"] and all(a == paper.account_id for a, _ in ev.items)
    assert ev.items[-1][1]["order"]["status"] == "FILLED"


# ---------------------------------------------------------------- 지난 장의 DAY 주문 (스케줄러 정지 시)
def test_stale_day_order_is_expired_not_filled_on_next_day(paper):
    """실제 발견된 문제(2026-10-21 데모): 15:30 만료 작업이 안 돌면
    어제 주문이 오늘 장에서 체결될 수 있었다."""
    oid = paper.place(buy(5, 70_000), asks((70_100, 10)))  # 10-21 장중 접수, 대기
    assert paper.order(oid).status == "PENDING"
    paper.event(asks((69_000, 100), seq=2), now=NEXT_OPEN)  # 다음 날 체결될 만한 호가
    o = paper.order(oid)
    assert o.status == "EXPIRED" and o.filled_qty == 0 and o.reserved_amount == 0
    assert paper.trades(oid) == [] and paper.account().reserved_cash == 0


def test_late_expire_job_catches_up_stale_orders(paper):
    oid = paper.place(buy(5, 69_000), asks((70_000, 10)))  # 10-21 장중
    after_hours = paper.place(buy(5, 69_000), mkt(None), now=CLOSED)  # 10-21 장 마감 후 -> 10-22 소속
    assert paper.expire(NEXT_OPEN) == 1  # 다음 날 아침에 늦게 돌아도 지난 장 주문만 만료
    assert paper.order(oid).status == "EXPIRED" and paper.order(after_hours).status == "PENDING"


def test_order_session_date_rules(paper, sf):
    from datetime import date, datetime

    from app.services.market_data import KST
    from app.services.paper.engine import order_session_date

    def d(*a):
        return datetime(*a, tzinfo=KST)

    with sf() as s:
        assert order_session_date(s, d(2026, 10, 21, 8, 0)) == date(2026, 10, 21)  # 개장 전 -> 당일
        assert order_session_date(s, d(2026, 10, 21, 15, 29)) == date(2026, 10, 21)  # 장중 -> 당일
        assert order_session_date(s, d(2026, 10, 21, 15, 30)) == date(2026, 10, 22)  # 마감 후 -> 다음 거래일
        assert order_session_date(s, d(2026, 10, 23, 20, 0)) == date(2026, 10, 26)  # 금요일 밤 -> 월요일
        assert order_session_date(s, d(2026, 12, 24, 16, 0)) == date(2026, 12, 28)  # 성탄절·주말 건너뜀
