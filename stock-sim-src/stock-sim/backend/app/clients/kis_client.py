"""한국투자증권 Open API (시세 조회 전용 — 주문 API 호출 금지, SPEC 1.3).

키: apiportal.koreainvestment.com. KIS_PAPER=True 면 모의투자 서버.
토큰은 하루 단위라 파일에 캐시한다(발급 횟수 제한 대비).

확인한 스펙(공식 샘플 koreainvestment/open-trading-api 기준):
- REST 현재가: GET /uapi/domestic-stock/v1/quotations/inquire-price, tr_id FHKST01010100 (실전/모의 동일)
- REST 호가  : GET /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn,
               tr_id FHKST01010200 (실전/모의 동일)
- 웹소켓 승인키: POST {REST}/oauth2/Approval  {grant_type, appkey, secretkey} -> approval_key
- 웹소켓 주소: 실전 ws://ops.koreainvestment.com:21000, 모의 ws://ops.koreainvestment.com:31000
               접속 경로 /tryitout
키와 토큰, 요청 헤더는 로그에 남기지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

REST_REAL = "https://openapi.koreainvestment.com:9443"
REST_PAPER = "https://openapivts.koreainvestment.com:29443"
WS_REAL = "ws://ops.koreainvestment.com:21000"
WS_PAPER = "ws://ops.koreainvestment.com:31000"
WS_PATH = "/tryitout"

TR_PRICE = "FHKST01010100"
TR_ASKING = "FHKST01010200"

TIMEOUT = 15
MAX_RETRIES = 3
BACKOFF = (1, 2, 4)


class KisError(RuntimeError):
    pass


class KisRest:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        paper: bool = True,
        token_file: Path | str = ".kis_token.json",
        min_interval: float | None = None,
        sleep=time.sleep,
    ) -> None:
        self.app_key, self.app_secret, self.paper = app_key, app_secret, paper
        self.base = REST_PAPER if paper else REST_REAL
        self.ws_url = (WS_PAPER if paper else WS_REAL) + WS_PATH
        self.token_file = Path(token_file)
        # 호출 간 최소 간격. 모의서버가 더 빡빡하다고 알려져 있음.  # TODO(verify): 공식 호출 제한 확인
        self.min_interval = min_interval if min_interval is not None else (0.5 if paper else 0.06)
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_call = 0.0

    @classmethod
    def from_env(cls) -> KisRest:
        return cls(
            os.getenv("KIS_APP_KEY", ""),
            os.getenv("KIS_APP_SECRET", ""),
            os.getenv("KIS_PAPER", "True").lower() == "true",
        )

    # --- 공통 ---
    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()

    def _send(self, method: str, url: str, **kw) -> dict:
        """타임아웃 15초, 재시도 3회(1/2/4초), 4xx(429 제외)는 재시도 없음 (SPEC 5)."""
        last: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            try:
                r = requests.request(method, url, timeout=TIMEOUT, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    raise KisError(f"HTTP {r.status_code}")
                if 400 <= r.status_code < 500:
                    raise KisError(f"HTTP {r.status_code} (재시도 안 함)")
                return r.json()
            except requests.RequestException as e:
                last = e
            except KisError as e:
                if "재시도 안 함" in str(e):
                    raise
                last = e
            if attempt < MAX_RETRIES:
                self._sleep(BACKOFF[attempt])
        raise KisError(f"KIS 요청 실패: {last.__class__.__name__}")  # 상세(헤더 등)는 남기지 않는다

    # --- 인증 ---
    def get_token(self) -> str:
        if self.token_file.exists():
            cache = json.loads(self.token_file.read_text())
            if cache.get("paper") == self.paper and cache["expires_at"] > time.time() + 60:
                return cache["token"]
        d = self._send(
            "POST",
            f"{self.base}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
        )
        if "access_token" not in d:
            raise KisError("KIS 토큰 발급 실패")
        self.token_file.write_text(
            json.dumps(
                {
                    "token": d["access_token"],
                    "paper": self.paper,
                    "expires_at": time.time() + int(d.get("expires_in", 86400)),
                }
            )
        )
        return d["access_token"]

    def get_approval_key(self) -> str:
        """웹소켓 접속용 approval key."""
        d = self._send(
            "POST",
            f"{self.base}/oauth2/Approval",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret},
        )
        key = d.get("approval_key")
        if not key:
            raise KisError("KIS approval_key 발급 실패")
        return key

    # --- 시세 조회 ---
    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        headers = {
            "authorization": f"Bearer {self.get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        d = self._send("GET", f"{self.base}{path}", headers=headers, params=params)
        if d.get("rt_cd") != "0":
            raise KisError(f"KIS 오류: {d.get('msg_cd')} {d.get('msg1')}")
        return d

    def current_price(self, code: str) -> dict:
        """output: stck_prpr(현재가), prdy_vrss(전일대비), prdy_ctrt(전일대비율), acml_vol(누적거래량),
        stck_sdpr(기준가), stck_mxpr(상한가), stck_llam(하한가) 등."""
        return self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            TR_PRICE,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )["output"]

    def orderbook(self, code: str) -> dict:
        """output1: 호가정보(askp1..10, bidp1..10, askp_rsqn1..10, bidp_rsqn1..10, aspr_acpt_hour 등),
        output2: 예상체결정보."""
        return self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            TR_ASKING,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )


# --- 기존 스크립트(scripts/test_apis.py) 호환용 모듈 함수 ---
_default: KisRest | None = None


def _client() -> KisRest:
    global _default
    if _default is None:
        _default = KisRest.from_env()  # import 시점이 아니라 호출 시점에 키를 읽는다
    return _default


def get_token() -> str:
    return _client().get_token()


def current_price(code: str) -> dict:
    return _client().current_price(code)


def orderbook(code: str) -> dict:
    return _client().orderbook(code)
