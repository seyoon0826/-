"""동시 주문에도 잔고/예약금이 깨지지 않음을 증명한다 (SPEC 16.1 필수 10, 7.9).

실제 Postgres 에 스레드로 동시 실행한다.
"""

from __future__ import annotations

import random
import threading
from collections import Counter

import pytest

from app.services.paper import engine
from app.services.paper.engine import Events
from tests.paper_support import OPEN, book, buy, mkt, sell, setup

N_THREADS = 20


@pytest.fixture
def paper(sf):
    p = setup(sf)
    yield p
    assert p.violations() == {}  # 필수 11


def run_concurrently(fns):
    """모든 스레드가 배리어에서 동시에 출발한다. 각 스레드는 자기 세션/트랜잭션을 쓴다."""
    barrier = threading.Barrier(len(fns))
    results, errors = [None] * len(fns), []

    def wrap(i, fn):
        try:
            barrier.wait()
            results[i] = fn()
        except Exception as e:  # pragma: no cover - 실패 시 원인 보고용
            errors.append(e)

    threads = [threading.Thread(target=wrap, args=(i, f)) for i, f in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    return results


def test_10_twenty_concurrent_1m_orders_on_10m_account_accept_exactly_10(paper):
    """가용 1,000만 원, 약 100만 원짜리 매수 20개 동시 -> 정확히 10개 접수, 나머지 INSUFFICIENT_CASH.
    99,900원 x 10주 = 999,000원 (수수료 포함 예약 999,177원). 10개 = 9,991,770원 <= 1,000만, 11개는 초과."""
    m = mkt(book(asks=[(100_000, 1_000)]), prev_close=100_000)  # 지정가보다 비싼 호가뿐 -> 체결 없이 대기

    def place():
        with paper.sf() as s:
            o = engine.place_order(s, paper.account_id, buy(10, 99_900), m, OPEN, Events())
            s.commit()
            return o.status, o.reject_reason

    results = run_concurrently([place] * N_THREADS)
    counts = Counter(results)
    assert counts[("PENDING", None)] == 10
    assert counts[("REJECTED", "INSUFFICIENT_CASH")] == 10
    a = paper.account()
    assert a.reserved_cash == 10 * 999_177 and a.cash == 10_000_000


def test_concurrent_sells_never_oversell(paper):
    """보유 10주에 2주 매도 20개 동시 -> 정확히 5개만 접수 (보유 - 예약수량 검사가 잠금 안에서 일어난다)."""
    oid = paper.place(buy(10, 70_000), mkt(book(asks=[(70_000, 10)], seq=1)))
    assert paper.order(oid).status == "FILLED"
    m = mkt(book(bids=[(60_000, 1_000)], seq=2))  # 매도 지정가보다 낮은 매수호가 -> 대기

    def place():
        with paper.sf() as s:
            o = engine.place_order(s, paper.account_id, sell(2, 75_000), m, OPEN, Events())
            s.commit()
            return o.status

    counts = Counter(run_concurrently([place] * N_THREADS))
    assert counts["PENDING"] == 5 and counts["REJECTED"] == 15
    assert paper.position().reserved_qty == 10


def test_concurrent_events_do_not_double_fill(paper):
    """같은 호가 이벤트를 여러 워커가 동시에 처리해도 체결은 한 번 (주문 행 잠금 + event_seq)."""
    oid = paper.place(buy(10, 70_000), mkt(book(asks=[(70_100, 1)], seq=1)))
    ev = mkt(book(asks=[(70_000, 4)], seq=2))

    def handle():
        with paper.sf() as s:
            n = len(engine.process_market_event(s, "005930", ev, OPEN, Events()))
            s.commit()
            return n

    run_concurrently([handle] * 10)
    assert paper.order(oid).filled_qty == 4 and len(paper.trades(oid)) == 1


def test_100_mixed_concurrent_operations_keep_ledger_invariants(paper):
    """주문/체결 이벤트/취소 100개를 섞어 동시에 실행해도 원장·예약 불변식이 유지된다.
    (Phase 4 프롬프트 요구: 동시 주문 100개)"""
    rng = random.Random(42)
    holding = paper.place(buy(50, 70_000), mkt(book(asks=[(70_000, 50)], seq=1)))
    assert paper.order(holding).status == "FILLED"
    seq = iter(range(100, 10_000))
    ops = []
    for _ in range(100):
        kind = rng.choice(["buy", "buy", "sell", "event", "cancel"])
        if kind == "buy":
            price = rng.choice([69_800, 69_900, 70_000])
            m = mkt(book(asks=[(70_100, 1_000)], seq=next(seq)))

            def op(price=price, m=m):
                with paper.sf() as s:
                    engine.place_order(s, paper.account_id, buy(rng.randint(1, 5), price), m, OPEN, Events())
                    s.commit()

        elif kind == "sell":
            m = mkt(book(bids=[(69_000, 1_000)], seq=next(seq)))

            def op(m=m):
                with paper.sf() as s:
                    engine.place_order(
                        s, paper.account_id, sell(rng.randint(1, 3), 70_500), m, OPEN, Events()
                    )
                    s.commit()

        elif kind == "event":
            m = mkt(book(asks=[(69_900, 3), (70_000, 3)], bids=[(70_500, 2)], seq=next(seq)))

            def op(m=m):
                with paper.sf() as s:
                    engine.process_market_event(s, "005930", m, OPEN, Events())
                    s.commit()

        else:

            def op():
                from sqlalchemy import select

                from app.models import Order

                with paper.sf() as s:
                    ids = list(
                        s.scalars(select(Order.id).where(Order.status.in_(("PENDING", "PARTIAL"))).limit(5))
                    )
                for oid in ids:
                    try:
                        with paper.sf() as s:
                            engine.cancel_order(s, paper.account_id, oid, OPEN, Events())
                            s.commit()
                    except engine.E.PaperError:
                        pass  # 그 사이 다른 스레드가 체결/취소함 -> 정상

        ops.append(op)
    run_concurrently(ops)
    assert paper.violations() == {}
    a = paper.account()
    assert 0 <= a.reserved_cash <= a.cash
