"""한국투자증권 Open API. 키: apiportal.koreainvestment.com
KIS_PAPER=True 면 모의투자 서버. 토큰은 하루 단위라 파일에 캐시(발급 횟수 제한 대비)."""
import json, os, time
from pathlib import Path
import requests

PAPER = os.getenv("KIS_PAPER", "True").lower() == "true"
BASE = "https://openapivts.koreainvestment.com:29443" if PAPER else "https://openapi.koreainvestment.com:9443"
APP_KEY, APP_SECRET = os.getenv("KIS_APP_KEY"), os.getenv("KIS_APP_SECRET")
TOKEN_FILE = Path(".kis_token.json")


def get_token() -> str:
    if TOKEN_FILE.exists():
        cache = json.loads(TOKEN_FILE.read_text())
        if cache.get("paper") == PAPER and cache["expires_at"] > time.time() + 60:
            return cache["token"]
    r = requests.post(f"{BASE}/oauth2/tokenP", timeout=15, json={
        "grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET})
    r.raise_for_status()
    d = r.json()
    TOKEN_FILE.write_text(json.dumps({
        "token": d["access_token"], "paper": PAPER,
        "expires_at": time.time() + int(d.get("expires_in", 86400))}))
    return d["access_token"]


def _get(path: str, tr_id: str, params: dict) -> dict:
    headers = {"authorization": f"Bearer {get_token()}", "appkey": APP_KEY,
               "appsecret": APP_SECRET, "tr_id": tr_id, "custtype": "P"}
    r = requests.get(f"{BASE}{path}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("rt_cd") != "0":
        raise RuntimeError(f"KIS 오류: {d.get('msg_cd')} {d.get('msg1')}")
    return d


def current_price(code: str) -> dict:
    """현재가. output: stck_prpr(현재가), prdy_ctrt(전일대비율), acml_vol(누적거래량) 등."""
    return _get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})["output"]


def orderbook(code: str) -> dict:
    """호가(매수/매도 1~10호가, 잔량). 모의투자 체결 엔진용."""
    return _get("/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                "FHKST01010200",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
