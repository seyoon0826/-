"""KIS Provider (FR-MD-4). 실제 KIS 에는 접속하지 않는다 — 로컬 가짜 웹소켓 서버와 스텁만 사용."""

import asyncio
import json
from datetime import datetime

import pytest
import requests
from websockets.asyncio.server import serve

from app.clients.kis_client import KisError, KisRest
from app.services.market_data import KST, MarketDataUnavailable, OrderBook, Quote
from app.services.providers import kis as kis_mod
from app.services.providers.kis import (
    ASKING_COLUMNS,
    CCNL_COLUMNS,
    KisProvider,
    parse_rest_orderbook,
    parse_rest_quote,
    parse_ws_frame,
    subscribe_message,
)

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=KST)


def ccnl_frame(code="005930", price=72_100, vrss=900, sign="2", acml=1_234):
    rec = ["0"] * len(CCNL_COLUMNS)
    fields = {
        "MKSC_SHRN_ISCD": code,
        "STCK_PRPR": price,
        "PRDY_VRSS_SIGN": sign,
        "PRDY_VRSS": vrss,
        "ACML_VOL": acml,
    }
    for k, v in fields.items():
        rec[CCNL_COLUMNS.index(k)] = str(v)
    return "0|H0STCNT0|001|" + "^".join(rec)


def asking_frame(code="005930", base=72_100, tick=100):
    rec = ["0"] * len(ASKING_COLUMNS)
    rec[0] = code
    for i in range(1, 11):
        rec[ASKING_COLUMNS.index(f"ASKP{i}")] = str(base + i * tick)
        rec[ASKING_COLUMNS.index(f"BIDP{i}")] = str(base - (i - 1) * tick)
        rec[ASKING_COLUMNS.index(f"ASKP_RSQN{i}")] = str(100 * i)
        rec[ASKING_COLUMNS.index(f"BIDP_RSQN{i}")] = str(10 * i)
    return "0|H0STASP0|001|" + "^".join(rec)


# ---------- 파싱 (순수 함수) ----------
def test_column_counts_match_official_sample():
    assert len(CCNL_COLUMNS) == 47 and len(ASKING_COLUMNS) == 63  # 공식 샘플 domestic_stock_functions_ws.py
    assert CCNL_COLUMNS[:3] == ["MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR"]


def test_parse_ws_ccnl_frame():
    [q] = parse_ws_frame(ccnl_frame(), NOW)
    assert isinstance(q, Quote)
    assert (q.code, q.price, q.change, q.prev_close, q.volume, q.source) == (
        "005930",
        72_100,
        900,
        71_200,
        1_234,
        "kis",
    )
    assert q.change_pct == 1.26


def test_parse_ws_fall_sign_is_negative():
    [q] = parse_ws_frame(ccnl_frame(vrss=1_000, sign="5"), NOW)
    assert q.change == -1_000 and q.prev_close == 73_100


def test_parse_ws_asking_frame():
    [ob] = parse_ws_frame(asking_frame(), NOW)
    assert isinstance(ob, OrderBook)
    assert ob.asks[0].price == 72_200 and ob.asks[0].qty == 100 and ob.asks[9].price == 73_100
    assert ob.bids[0].price == 72_100 and ob.bids[0].qty == 10 and ob.bids[9].qty == 100


def test_parse_ws_multiple_records_in_one_frame():
    a, b = ccnl_frame("005930"), ccnl_frame("000660", price=198_500)
    payload = a.split("|", 3)[3] + "^" + b.split("|", 3)[3]
    out = parse_ws_frame("0|H0STCNT0|002|" + payload, NOW)
    assert [q.code for q in out] == ["005930", "000660"] and out[1].price == 198_500


@pytest.mark.parametrize(
    "text",
    ["", '{"header":{"tr_id":"PINGPONG"}}', "0|H0STXXXX|001|a^b", "0|H0STCNT0|001|a^b", "1|H0STCNI0|001|enc"],
)
def test_parse_ws_ignores_control_unknown_short_and_encrypted(text):
    assert parse_ws_frame(text, NOW) == []


def test_subscribe_message_shape():
    msg = json.loads(subscribe_message("KEY", "H0STCNT0", "005930"))
    assert msg["header"] == {"approval_key": "KEY", "custtype": "P", "tr_type": "1", "content-type": "utf-8"}
    assert msg["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "005930"}


def test_parse_rest_quote_and_orderbook():
    q = parse_rest_quote(
        "005930", {"stck_prpr": "72100", "prdy_vrss": "900", "prdy_vrss_sign": "2", "acml_vol": "55"}, NOW
    )
    assert (q.price, q.change, q.volume) == (72_100, 900, 55)
    o1 = {}
    for i in range(1, 11):
        o1.update({f"askp{i}": str(72_100 + i * 100), f"bidp{i}": str(72_100 - (i - 1) * 100)})
        o1.update({f"askp_rsqn{i}": "5", f"bidp_rsqn{i}": "7"})
    ob = parse_rest_orderbook("005930", {"output1": o1, "output2": {}}, NOW)
    assert len(ob.asks) == len(ob.bids) == 10 and ob.asks[0].price == 72_200 and ob.bids[0].qty == 7


def test_parse_rest_orderbook_drops_empty_levels():
    o1 = {f"{k}{i}": "0" for i in range(1, 11) for k in ("askp", "bidp", "askp_rsqn", "bidp_rsqn")}
    o1.update({"askp1": "101", "askp_rsqn1": "3", "bidp1": "99", "bidp_rsqn1": "4"})
    ob = parse_rest_orderbook("X", {"output1": o1}, NOW)
    assert len(ob.asks) == len(ob.bids) == 1


# ---------- KisRest: 재시도/레이트 리밋/키 비노출 ----------
class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


def _rest(tmp_path, sleeps):
    return KisRest("APPKEY", "SECRETVALUE", True, tmp_path / "tok.json", min_interval=0, sleep=sleeps.append)


def test_rest_retries_with_backoff_then_succeeds(tmp_path, monkeypatch):
    sleeps, calls = [], []

    def fake(method, url, **kw):
        calls.append(url)
        if len(calls) < 3:
            raise requests.ConnectionError("boom")
        return FakeResp(200, {"approval_key": "AK"})

    monkeypatch.setattr(requests, "request", fake)
    assert _rest(tmp_path, sleeps).get_approval_key() == "AK"
    assert sleeps == [1, 2] and len(calls) == 3  # SPEC 5: 1s/2s/4s 지수 백오프


def test_rest_gives_up_after_3_retries_without_leaking_keys(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResp(503))
    with pytest.raises(KisError) as e:
        _rest(tmp_path, sleeps).get_approval_key()
    assert sleeps == [1, 2, 4]
    assert "APPKEY" not in str(e.value) and "SECRETVALUE" not in str(e.value)


def test_rest_does_not_retry_4xx_but_retries_429(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: calls.append(1) or FakeResp(400))
    with pytest.raises(KisError):
        _rest(tmp_path, []).get_approval_key()
    assert len(calls) == 1
    calls.clear()
    monkeypatch.setattr(requests, "request", lambda *a, **k: calls.append(1) or FakeResp(429))
    with pytest.raises(KisError):
        _rest(tmp_path, []).get_approval_key()
    assert len(calls) == 4


def test_rest_urls_and_paper_switch(tmp_path):
    paper = KisRest("k", "s", True, tmp_path / "t")
    real = KisRest("k", "s", False, tmp_path / "t")
    assert paper.base.endswith(":29443") and paper.ws_url == "ws://ops.koreainvestment.com:31000/tryitout"
    assert real.base.endswith(":9443") and real.ws_url == "ws://ops.koreainvestment.com:21000/tryitout"


def test_token_is_cached_in_file(tmp_path, monkeypatch):
    calls = []

    def fake(method, url, **kw):
        calls.append(url)
        return FakeResp(200, {"access_token": "TKN", "expires_in": 86400})

    monkeypatch.setattr(requests, "request", fake)
    r = _rest(tmp_path, [])
    assert r.get_token() == "TKN" and r.get_token() == "TKN"
    assert len(calls) == 1


# ---------- KisProvider ----------
class StubRest:
    ws_url = "ws://unused"

    def __init__(self):
        self.approval_calls = 0

    def get_approval_key(self):
        self.approval_calls += 1
        return "KEY"

    def current_price(self, code):
        return {"stck_prpr": "1000", "prdy_vrss": "10", "prdy_vrss_sign": "2", "acml_vol": "5"}

    def orderbook(self, code):
        o1 = {}
        for i in range(1, 11):
            o1.update(
                {
                    f"askp{i}": str(1000 + i),
                    f"bidp{i}": str(1000 - i),
                    f"askp_rsqn{i}": "1",
                    f"bidp_rsqn{i}": "1",
                }
            )
        return {"output1": o1}


async def test_provider_snapshot_via_rest():
    p = KisProvider(StubRest())
    assert (await p.get_quote("005930")).price == 1_000
    assert (await p.get_orderbook("005930")).asks[0].price == 1_001


async def test_provider_snapshot_failure_becomes_unavailable():
    class Bad(StubRest):
        def current_price(self, code):
            raise KisError("x")

    with pytest.raises(MarketDataUnavailable):
        await KisProvider(Bad()).get_quote("005930")


async def test_ws_reconnects_and_restores_subscriptions_FR_MD_4():
    registrations: list[list[tuple[str, str]]] = []  # 연결별 등록 목록

    async def handler(ws):
        regs = []
        registrations.append(regs)
        async for raw in ws:
            body = json.loads(raw)["body"]["input"]
            regs.append((body["tr_id"], body["tr_key"]))
            if len(regs) == 2:  # 종목 1개 = TR 2개 등록 완료
                await ws.send(ccnl_frame())
                await ws.send(asking_frame())
                if len(registrations) == 1:
                    await ws.close()  # 첫 연결은 서버가 끊는다

    quotes, books = [], []

    async def on_q(q):
        quotes.append(q)

    async def on_b(b):
        books.append(b)

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        rest = StubRest()
        p = KisProvider(rest, backoff=(0.01,), ws_url=f"ws://127.0.0.1:{port}", poll_interval=60)
        await p.subscribe(["005930"], on_q, on_b)
        for _ in range(300):
            ws_books = [
                b for b in books if b.asks[0].price == 72_200
            ]  # 웹소켓 프레임에서 온 값 (REST 스텁은 1,001)
            if len(registrations) >= 2 and len(ws_books) >= 2:
                break
            await asyncio.sleep(0.02)
        await p.close()

    assert len(ws_books) >= 2  # 재연결 후에도 웹소켓 데이터 수신
    assert len(registrations) >= 2
    expected = [("H0STCNT0", "005930"), ("H0STASP0", "005930")]
    assert registrations[0] == expected and registrations[1] == expected  # 구독 복구
    assert rest.approval_calls >= 2


async def test_rest_polling_covers_codes_beyond_subscription_limit(monkeypatch):
    """등록 한도(2 = 종목 1개)를 넘는 종목은 REST 폴링, 웹소켓이 안 붙으면 전부 폴링으로 강등."""

    async def refuse(*a, **k):
        raise OSError("연결 불가")

    class NoWs:
        def __call__(self, url):
            raise OSError("연결 불가")

    quotes = []

    async def on_q(q):
        quotes.append(q.code)

    async def on_b(b):
        pass

    p = KisProvider(StubRest(), max_subscriptions=2, poll_interval=0.01, backoff=(0.5,), connect=NoWs())
    await p.subscribe(["AAA", "BBB"], on_q, on_b)
    for _ in range(100):
        if {"AAA", "BBB"} <= set(quotes):
            break
        await asyncio.sleep(0.02)
    assert p.degraded is True  # 웹소켓 미연결 = 강등
    await p.close()
    assert {"AAA", "BBB"} <= set(quotes)
    assert p._ws_codes == ["AAA"] and p._rest_codes == ["BBB"]


async def test_priority_codes_get_websocket_slots_first():
    p = KisProvider(StubRest(), max_subscriptions=4, priority=["CCC"])
    p._split_codes(["AAA", "BBB", "CCC", "DDD"])
    assert p._ws_codes == ["CCC", "AAA"] and p._rest_codes == ["BBB", "DDD"]


async def test_degraded_on_silence_only_during_market_hours(monkeypatch):
    p = KisProvider(StubRest(), silence_sec=30)
    p._connected = True
    p._last_msg = 0.0  # 아주 오래 전
    monkeypatch.setattr(kis_mod, "market_state", lambda now: "open")
    assert p.degraded is True
    monkeypatch.setattr(kis_mod, "market_state", lambda now: "closed")
    assert p.degraded is False  # 장 외에는 메시지가 없는 게 정상
