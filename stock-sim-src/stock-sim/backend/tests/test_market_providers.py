"""PykrxEodProvider / ReplayProvider (FR-MD-2, FR-MD-3). 네트워크 없음."""

from datetime import date

import pytest

from app.services.market_data import MarketDataUnavailable
from app.services.providers._book import synth_orderbook
from app.services.providers.pykrx_eod import EodBar, PykrxEodProvider
from app.services.providers.replay import DailyBar, ReplayProvider, bars_to_events
from app.services.ticks import is_valid_tick


def _collector():
    quotes, books = [], []

    async def on_q(q):
        quotes.append(q)

    async def on_b(b):
        books.append(b)

    return quotes, books, on_q, on_b


async def test_synth_orderbook_one_tick_around_close_FR_MD_2():
    from datetime import datetime

    ob = synth_orderbook("005930", 72_100, 10_000, datetime(2026, 10, 20), "pykrx")
    assert ob.asks[0].price == 72_200 and ob.bids[0].price == 72_000  # 종가 ±1틱
    assert len(ob.asks) == len(ob.bids) == 10
    assert all(lv.qty == 10_000 for lv in ob.asks + ob.bids)
    assert [lv.price for lv in ob.asks] == sorted(lv.price for lv in ob.asks)
    assert [lv.price for lv in ob.bids] == sorted((lv.price for lv in ob.bids), reverse=True)
    assert all(is_valid_tick(lv.price) for lv in ob.asks + ob.bids)
    assert ob.asks[0].price > ob.bids[0].price


async def test_synth_orderbook_crosses_tick_boundary():
    from datetime import datetime

    ob = synth_orderbook("X", 1_998, 1, datetime(2026, 1, 1), "pykrx")
    assert [lv.price for lv in ob.asks[:3]] == [1_999, 2_000, 2_005]


async def test_pykrx_eod_quote_and_book_from_source():
    bars = {"005930": EodBar("005930", date(2026, 10, 20), 72_100, 71_200, 12_000_000)}
    p = PykrxEodProvider(price_source=lambda c: bars[c], depth_qty=5_000)
    q = await p.get_quote("005930")
    assert (q.price, q.change, q.change_pct, q.volume, q.source) == (72_100, 900, 1.26, 12_000_000, "pykrx")
    ob = await p.get_orderbook("005930")
    assert ob.asks[0].qty == 5_000 and ob.asks[0].price == 72_200


async def test_pykrx_eod_subscribe_emits_once_and_skips_failures():
    def source(code):
        if code == "BAD":
            raise MarketDataUnavailable("없음")
        return EodBar(code, date(2026, 10, 20), 10_000, None, 1)

    quotes, books, on_q, on_b = _collector()
    p = PykrxEodProvider(price_source=source)
    await p.subscribe(["005930", "BAD", "000660"], on_q, on_b)  # 일부 실패해도 계속
    assert [q.code for q in quotes] == ["005930", "000660"]
    assert len(books) == 2
    assert quotes[0].change == 0 and quotes[0].prev_close is None
    await p.close()


def _bars():
    d1, d2 = date(2026, 10, 19), date(2026, 10, 20)
    return {
        "A": [DailyBar("A", d1, 100, 110, 95, 105, 1_000), DailyBar("A", d2, 105, 108, 100, 102, 2_000)],
        "B": [DailyBar("B", d1, 50, 55, 48, 52, 400)],
    }


async def test_replay_is_deterministic_and_ordered_FR_MD_3():
    runs = []
    for _ in range(2):
        quotes, books, on_q, on_b = _collector()
        p = ReplayProvider.from_bars(_bars(), speed=0)  # 대기 없이 즉시 재생
        await p.subscribe(["A", "B"], on_q, on_b)
        runs.append([(q.code, q.price, q.volume) for q in quotes])
    assert runs[0] == runs[1]
    a_prices = [x[1] for x in runs[0] if x[0] == "A"]
    # 양봉: 시가 -> 저가 -> 고가 -> 종가, 음봉: 시가 -> 고가 -> 저가 -> 종가
    assert a_prices == [100, 95, 110, 105, 105, 108, 100, 102]
    assert [x[2] for x in runs[0] if x[0] == "A"][:4] == [250, 500, 750, 1_000]  # 누적거래량


async def test_replay_change_uses_previous_close_and_tracks_latest():
    quotes, _, on_q, on_b = _collector()
    p = ReplayProvider.from_bars(_bars(), speed=0)
    await p.subscribe(["A"], on_q, on_b)
    second_day_open = [q for q in quotes if q.code == "A"][4]
    assert second_day_open.prev_close == 105
    latest = await p.get_quote("A")
    assert latest.price == 102 and latest.source == "replay"
    with pytest.raises(MarketDataUnavailable):
        await p.get_quote("ZZZ")


async def test_replay_only_requested_codes_and_books_match_quotes():
    quotes, books, on_q, on_b = _collector()
    p = ReplayProvider.from_bars(_bars(), speed=0)
    await p.subscribe(["B"], on_q, on_b)
    assert {q.code for q in quotes} == {"B"} and len(books) == len(quotes) == 4
    assert all(b.asks[0].price > q.price > b.bids[0].price for q, b in zip(quotes, books, strict=True))


def test_bars_to_events_sorted_by_time():
    ev = bars_to_events(_bars())
    assert [e.t for e in ev] == sorted(e.t for e in ev)


async def test_synth_orderbook_from_off_tick_adjusted_price_is_orderable():
    """과거 수정주가(예: 034020 2018-01-02 시가 11,703원)는 호가 단위에 안 맞는다.
    합성 호가는 모두 주문 가능한 가격이어야 하고, 기준가를 사이에 두어야 한다."""
    from datetime import datetime

    ob = synth_orderbook("034020", 11_703, 1, datetime(2018, 1, 2), "replay")
    assert ob.asks[0].price == 11_710 and ob.bids[0].price == 11_700
    assert all(is_valid_tick(lv.price) for lv in ob.asks + ob.bids)
    assert ob.bids[0].price < 11_703 < ob.asks[0].price
