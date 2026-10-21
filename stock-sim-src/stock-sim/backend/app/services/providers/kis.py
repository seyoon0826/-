"""KIS Provider (FR-MD-4). 시세 조회 전용 — 주문 API 는 호출하지 않는다.

- REST: 현재가/호가 스냅샷 (kis_client.KisRest)
- 웹소켓: 실시간 체결가(H0STCNT0)와 호가(H0STASP0). 실전/모의 TR ID 동일(공식 샘플 확인).
- 끊기면 지수 백오프로 재연결하고 구독을 복구한다. 장중 30초간 메시지가 없으면 REST 폴링으로 강등한다.
- 등록 한도(체결+호가 TR 합산)가 있으므로 종목당 TR 2개를 쓴다. 한도를 넘는 종목은 REST 폴링으로 처리한다.
  우선순위 종목(예: 화면에서 보는 종목)을 먼저 구독한다 (SPEC Q3 기본안).

TODO(verify): 등록 한도(공식 샘플 러너는 40, 일부 자료는 20), 모의 도메인의 실시간 시세 제공 범위,
              REST 호가 응답 필드의 단위/부호. 실제 키로 검증하지 못했다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Sequence
from datetime import datetime

import websockets

from app.clients.kis_client import KisRest
from app.core.config import Settings
from app.services.market_data import (
    DEPTH,
    KST,
    BookLevel,
    MarketDataProvider,
    MarketDataUnavailable,
    OrderBook,
    OrderBookCallback,
    Quote,
    QuoteCallback,
    make_quote,
)
from app.services.market_hours import market_state

log = logging.getLogger(__name__)

TR_CCNL = "H0STCNT0"  # 실시간 체결가
TR_ASKING = "H0STASP0"  # 실시간 호가

# 공식 샘플(domestic_stock_functions_ws.py)의 컬럼 순서
CCNL_COLUMNS = (
    "MKSC_SHRN_ISCD,STCK_CNTG_HOUR,STCK_PRPR,PRDY_VRSS_SIGN,PRDY_VRSS,PRDY_CTRT,WGHN_AVRG_STCK_PRC,STCK_OPRC,"
    "STCK_HGPR,STCK_LWPR,ASKP1,BIDP1,CNTG_VOL,ACML_VOL,ACML_TR_PBMN,SELN_CNTG_CSNU,SHNU_CNTG_CSNU,NTBY_CNTG_CSNU,"
    "CTTR,SELN_CNTG_SMTN,SHNU_CNTG_SMTN,CCLD_DVSN,SHNU_RATE,PRDY_VOL_VRSS_ACML_VOL_RATE,OPRC_HOUR,"
    "OPRC_VRSS_PRPR_SIGN,OPRC_VRSS_PRPR,HGPR_HOUR,HGPR_VRSS_PRPR_SIGN,HGPR_VRSS_PRPR,LWPR_HOUR,"
    "LWPR_VRSS_PRPR_SIGN,LWPR_VRSS_PRPR,BSOP_DATE,NEW_MKOP_CLS_CODE,TRHT_YN,ASKP_RSQN1,BIDP_RSQN1,"
    "TOTAL_ASKP_RSQN,TOTAL_BIDP_RSQN,VOL_TNRT,PRDY_SMNS_HOUR_ACML_VOL,PRDY_SMNS_HOUR_ACML_VOL_RATE,"
    "HOUR_CLS_CODE,MRKT_TRTM_CLS_CODE,VI_STND_PRC,MARKET_CLS_CODE"
).split(",")

ASKING_COLUMNS = (
    ["MKSC_SHRN_ISCD", "BSOP_HOUR", "HOUR_CLS_CODE"]
    + [f"ASKP{i}" for i in range(1, 11)]
    + [f"BIDP{i}" for i in range(1, 11)]
    + [f"ASKP_RSQN{i}" for i in range(1, 11)]
    + [f"BIDP_RSQN{i}" for i in range(1, 11)]
    + "TOTAL_ASKP_RSQN,TOTAL_BIDP_RSQN,OVTM_TOTAL_ASKP_RSQN,OVTM_TOTAL_BIDP_RSQN,ANTC_CNPR,ANTC_CNQN,"
    "ANTC_VOL,"
    "ANTC_CNTG_VRSS,ANTC_CNTG_VRSS_SIGN,ANTC_CNTG_PRDY_CTRT,ACML_VOL,TOTAL_ASKP_RSQN_ICDC,"
    "TOTAL_BIDP_RSQN_ICDC,OVTM_TOTAL_ASKP_ICDC,OVTM_TOTAL_BIDP_ICDC,STCK_DEAL_CLS_CODE,MID_PRC,"
    "MIDP_TOTAL_RSQN,MIDP_CLS_CODE,MARKET_CLS_CODE".split(",")
)

_COLUMNS = {TR_CCNL: CCNL_COLUMNS, TR_ASKING: ASKING_COLUMNS}
_FALL_SIGNS = ("4", "5")  # 1 상한, 2 상승, 3 보합, 4 하한, 5 하락


def _signed(value: str, sign: str) -> int:
    n = abs(int(float(value or 0)))
    return -n if sign in _FALL_SIGNS else n


# --- REST 응답 파싱 ---
def parse_rest_quote(code: str, output: dict, now: datetime) -> Quote:
    price = int(output["stck_prpr"])
    change = _signed(output.get("prdy_vrss", "0"), output.get("prdy_vrss_sign", "3"))
    prev_close = price - change
    return make_quote(code, price, prev_close, int(output.get("acml_vol") or 0), now, "kis")


def parse_rest_orderbook(code: str, data: dict, now: datetime) -> OrderBook:
    o = data["output1"]
    asks = tuple(BookLevel(int(o[f"askp{i}"]), int(o[f"askp_rsqn{i}"])) for i in range(1, DEPTH + 1))
    bids = tuple(BookLevel(int(o[f"bidp{i}"]), int(o[f"bidp_rsqn{i}"])) for i in range(1, DEPTH + 1))
    # 값이 0인 단(호가 없음)은 버린다
    return OrderBook(
        code, tuple(x for x in asks if x.price > 0), tuple(x for x in bids if x.price > 0), now, "kis"
    )


# --- 웹소켓 프레임 파싱 ---
def parse_ws_frame(text: str, now: datetime) -> list[Quote | OrderBook]:
    """데이터 프레임 `0|TR_ID|건수|필드^필드^...` 을 해석한다. 제어 프레임(JSON)이나 알 수 없는 것은 []."""
    if not text or text[0] not in "01" or "|" not in text:
        return []
    if text[0] == "1":  # 암호화 프레임은 체결통보 전용. 우리는 구독하지 않는다
        log.warning("암호화된 실시간 프레임을 무시합니다")
        return []
    parts = text.split("|", 3)
    if len(parts) < 4:
        return []
    _, tr_id, count, payload = parts
    cols = _COLUMNS.get(tr_id)
    if cols is None:
        return []
    fields = payload.split("^")
    n = max(int(count), 1)
    per = len(fields) // n
    if per < len(cols):
        log.warning("%s 프레임 필드 수가 예상보다 적습니다 (%d < %d)", tr_id, per, len(cols))
        return []
    out: list[Quote | OrderBook] = []
    for i in range(n):
        rec = dict(zip(cols, fields[i * per : (i + 1) * per], strict=False))
        code = rec["MKSC_SHRN_ISCD"]
        if tr_id == TR_CCNL:
            price = int(rec["STCK_PRPR"])
            change = _signed(rec["PRDY_VRSS"], rec["PRDY_VRSS_SIGN"])
            out.append(make_quote(code, price, price - change, int(rec["ACML_VOL"]), now, "kis"))
        else:
            asks = tuple(
                BookLevel(int(rec[f"ASKP{k}"]), int(rec[f"ASKP_RSQN{k}"])) for k in range(1, DEPTH + 1)
            )
            bids = tuple(
                BookLevel(int(rec[f"BIDP{k}"]), int(rec[f"BIDP_RSQN{k}"])) for k in range(1, DEPTH + 1)
            )
            out.append(
                OrderBook(
                    code,
                    tuple(x for x in asks if x.price > 0),
                    tuple(x for x in bids if x.price > 0),
                    now,
                    "kis",
                )
            )
    return out


def subscribe_message(approval_key: str, tr_id: str, code: str) -> str:
    """등록 요청. 해제 코드는 문서 간 표기가 달라(0/2) 쓰지 않고 소켓을 닫아 해제한다.  # TODO(verify)"""
    return json.dumps(
        {
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": tr_id, "tr_key": code}},
        }
    )


class KisProvider(MarketDataProvider):
    name = "kis"

    def __init__(
        self,
        rest: KisRest,
        max_subscriptions: int = 40,
        poll_interval: float = 3.0,
        silence_sec: float = 30.0,
        backoff: Sequence[float] = (1, 2, 4, 8, 16, 30),
        connect: Callable = websockets.connect,
        ws_url: str | None = None,
        priority: Sequence[str] = (),
    ) -> None:
        self.rest = rest
        self._max_subs = max_subscriptions
        self._poll = poll_interval
        self._silence = silence_sec
        self._backoff = tuple(backoff)
        self._connect = connect
        self._ws_url = ws_url or rest.ws_url
        self._priority = list(priority)
        self._tasks: list[asyncio.Task] = []
        self._closing = False
        self._connected = False
        self._last_msg = time.monotonic()
        self._ws_codes: list[str] = []
        self._rest_codes: list[str] = []

    @classmethod
    def from_settings(cls, cfg: Settings) -> KisProvider:
        rest = KisRest(cfg.kis_app_key, cfg.kis_app_secret, cfg.kis_paper)
        return cls(
            rest,
            max_subscriptions=cfg.kis_max_subscriptions,
            poll_interval=cfg.kis_poll_interval_sec,
            silence_sec=cfg.kis_ws_silence_sec,
        )

    def set_priority(self, codes: Sequence[str]) -> None:
        """화면에서 보는 종목을 먼저 구독하도록 한다 (다음 (재)연결부터 반영)."""
        self._priority = list(codes)

    @property
    def degraded(self) -> bool:
        if not self._connected:
            return True
        silent = time.monotonic() - self._last_msg > self._silence
        return silent and market_state(datetime.now(KST)) == "open"

    # --- 스냅샷 ---
    async def get_quote(self, code: str) -> Quote:
        try:
            out = await asyncio.to_thread(self.rest.current_price, code)
        except Exception as e:
            raise MarketDataUnavailable(f"KIS 현재가 조회 실패: {e}") from e
        return parse_rest_quote(code, out, datetime.now(KST))

    async def get_orderbook(self, code: str) -> OrderBook:
        try:
            data = await asyncio.to_thread(self.rest.orderbook, code)
        except Exception as e:
            raise MarketDataUnavailable(f"KIS 호가 조회 실패: {e}") from e
        return parse_rest_orderbook(code, data, datetime.now(KST))

    # --- 스트림 ---
    def _split_codes(self, codes: Sequence[str]) -> None:
        ordered = [c for c in self._priority if c in codes] + [c for c in codes if c not in self._priority]
        slots = self._max_subs // 2  # 종목당 TR 2개(체결+호가)
        self._ws_codes, self._rest_codes = ordered[:slots], ordered[slots:]
        if self._rest_codes:
            log.warning("등록 한도로 %d종목은 REST 폴링으로 처리합니다", len(self._rest_codes))

    async def subscribe(
        self, codes: Sequence[str], on_quote: QuoteCallback, on_orderbook: OrderBookCallback
    ) -> None:
        self._split_codes(codes)
        self._tasks = [
            asyncio.create_task(self._ws_loop(on_quote, on_orderbook)),
            asyncio.create_task(self._poll_loop(on_quote, on_orderbook)),
        ]

    async def _ws_loop(self, on_quote: QuoteCallback, on_orderbook: OrderBookCallback) -> None:
        attempt = 0
        while not self._closing:
            try:
                key = await asyncio.to_thread(self.rest.get_approval_key)
                async with self._connect(self._ws_url) as ws:
                    for code in self._ws_codes:  # 재연결 시에도 전부 다시 등록 (구독 복구)
                        for tr in (TR_CCNL, TR_ASKING):
                            await ws.send(subscribe_message(key, tr, code))
                    self._connected = True
                    self._last_msg = time.monotonic()
                    attempt = 0
                    async for raw in ws:
                        text = raw.decode() if isinstance(raw, bytes) else raw
                        self._last_msg = time.monotonic()
                        if text.startswith("{"):
                            await self._on_control(ws, text)
                            continue
                        for item in parse_ws_frame(text, datetime.now(KST)):
                            if isinstance(item, Quote):
                                await on_quote(item)
                            else:
                                await on_orderbook(item)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 키/헤더 등 상세는 남기지 않는다
                log.warning("KIS 웹소켓 끊김: %s", e.__class__.__name__)
            self._connected = False
            if self._closing:
                break
            await asyncio.sleep(self._backoff[min(attempt, len(self._backoff) - 1)])
            attempt += 1

    async def _on_control(self, ws, text: str) -> None:
        try:
            msg = json.loads(text)
        except ValueError:
            return
        tr_id = msg.get("header", {}).get("tr_id")
        if tr_id == "PINGPONG":
            await ws.pong(text.encode())
            return
        body = msg.get("body") or {}
        if body.get("rt_cd") not in (None, "0"):
            log.warning("KIS 구독 응답 오류: %s %s", body.get("msg_cd"), body.get("msg1"))

    async def _poll_loop(self, on_quote: QuoteCallback, on_orderbook: OrderBookCallback) -> None:
        """한도 밖 종목은 항상, 웹소켓 종목은 강등(degraded) 상태일 때만 REST 로 갱신한다."""
        while not self._closing:
            targets = list(self._rest_codes) + (list(self._ws_codes) if self.degraded else [])
            for code in targets:
                try:
                    await on_quote(await self.get_quote(code))
                    await on_orderbook(await self.get_orderbook(code))
                except MarketDataUnavailable as e:
                    log.warning("%s REST 폴링 실패: %s", code, e)
                except asyncio.CancelledError:
                    raise
            await asyncio.sleep(self._poll)

    async def close(self) -> None:
        self._closing = True
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
