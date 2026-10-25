"""pykrx: 일봉, 시총, 투자자별 수급, 지수. (pykrx 1.2.x 는 KRX 회원 로그인(KRX_ID/KRX_PW)이 필요하다.)

실제 응답으로 확인한 컬럼(2026-10-21):
- get_market_ohlcv_by_date: 시가 고가 저가 종가 거래량 등락률
- get_market_cap_by_date: 시가총액 거래량 거래대금 상장주식수
- get_market_trading_value_by_date: 기관합계 기타법인 개인 외국인합계 전체
- get_index_ohlcv_by_date(.., "1001"): 시가 고가 저가 종가 거래량 거래대금 상장시가총액
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import pandas as pd

KOSPI_INDEX = "1001"


HTTP_TIMEOUT = (10, 60)  # (연결, 읽기) 초


def _install_default_timeout() -> None:
    """pykrx 데이터 요청은 timeout 없이 requests 를 불러 서버가 응답을 멈추면 영원히 기다린다
    (실제로 SSL read 에서 1시간 가까이 멈춘 적 있음). timeout 을 안 준 요청에만 기본값을 넣는다."""
    import requests

    if getattr(requests.Session.request, "_stocksim_timeout", False):
        return
    orig = requests.Session.request

    def request(self, method, url, **kw):
        if kw.get("timeout") is None:
            kw["timeout"] = HTTP_TIMEOUT
        return orig(self, method, url, **kw)

    request._stocksim_timeout = True  # type: ignore[attr-defined]
    requests.Session.request = request  # type: ignore[method-assign]


def _stock():
    """pykrx 는 import 시점에 KRX 로그인을 시도하므로 쓸 때까지 import 를 미룬다(테스트는 네트워크 없이)."""
    _install_default_timeout()
    from pykrx import stock

    return stock


RETRIES = (1, 2, 4)


def _retry(fn, *args, **kw):
    """KRX 일시 장애 대비: 3회 재시도(1/2/4초)."""
    for i in range(len(RETRIES) + 1):
        try:
            return fn(*args, **kw)
        except Exception:
            if i == len(RETRIES):
                raise
            time.sleep(RETRIES[i])


def is_preferred(code: str) -> bool:
    """우선주 판별: 보통주 코드는 끝자리가 0 이다(삼성전자 005930, 삼성전자우 005935).
    TODO(verify): 신형 영숫자 코드(예: 0001A0)도 보통주는 0 으로 끝나는지 확인 필요."""
    return not code.endswith("0")


def latest_business_day(before: date | None = None) -> str:
    """기준일(기본 오늘) 이하에서 가장 최근 영업일(YYYYMMDD)."""
    d = datetime.combine(before, datetime.min.time()) if before else datetime.now()
    for _ in range(10):
        s = d.strftime("%Y%m%d")
        if not _retry(_stock().get_market_ohlcv_by_ticker, s, market="KOSPI").empty:
            return s
        d -= timedelta(days=1)
    raise RuntimeError("영업일을 찾지 못했습니다")


def top_marketcap(n: int = 30, date: str | None = None, include_preferred: bool = True) -> pd.DataFrame:
    """코스피 시총 상위 n종목 (code, name, 시가총액)."""
    date = date or latest_business_day()
    df = _retry(_stock().get_market_cap_by_ticker, date, market="KOSPI")
    df = df.sort_values("시가총액", ascending=False)
    if not include_preferred:
        df = df[[not is_preferred(t) for t in df.index]]
    df = df.head(n).copy()
    df["name"] = [_retry(_stock().get_market_ticker_name, t) for t in df.index]
    return df.reset_index().rename(columns={"티커": "code"})[["code", "name", "시가총액"]]


def _year_windows(start: str, end: str):
    s, e = datetime.strptime(start, "%Y%m%d").date(), datetime.strptime(end, "%Y%m%d").date()
    while s <= e:
        w_end = min(date(s.year, 12, 31), e)
        yield s.strftime("%Y%m%d"), w_end.strftime("%Y%m%d")
        s = w_end + timedelta(days=1)


def daily_ohlcv(code: str, start: str, end: str) -> pd.DataFrame:
    """일봉. start/end 는 'YYYYMMDD'."""
    df = _retry(_stock().get_market_ohlcv_by_date, start, end, code)
    return df.rename(
        columns={"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}
    )[["open", "high", "low", "close", "volume"]]


def investor_net(code: str, start: str, end: str) -> pd.DataFrame:
    """투자자별 순매수 금액(외국인, 기관)."""
    df = _retry(_stock().get_market_trading_value_by_date, start, end, code)
    if "외국인합계" not in df.columns:  # 거래정지 등으로 KRX 가 빈 응답을 주면 수급만 비운다 (가격은 저장)
        return pd.DataFrame(columns=["foreign_net", "inst_net"], dtype=float)
    return df.rename(columns={"외국인합계": "foreign_net", "기관합계": "inst_net"})[
        ["foreign_net", "inst_net"]
    ]


def valuation(code: str, start: str, end: str) -> pd.DataFrame:
    """KRX 일별 PER/PBR/배당수익률(%). 펀드·리츠 등 값이 없는 종목은 빈 표 (가격은 그대로 저장)."""
    df = _retry(_stock().get_market_fundamental, start, end, code)
    if not {"PER", "PBR", "DIV"} <= set(df.columns):
        return pd.DataFrame(columns=["per", "pbr", "div_yield"], dtype=float)
    return df.rename(columns={"PER": "per", "PBR": "pbr", "DIV": "div_yield"})[["per", "pbr", "div_yield"]]


def collect(code: str, start: str, end: str) -> pd.DataFrame:
    """일봉 + 수급 합치기 (CSV 수집 스크립트용)."""
    out = daily_ohlcv(code, start, end).join(investor_net(code, start, end))
    out.insert(0, "code", code)
    return out


def collect_full(code: str, start: str, end: str, pause: float = 0.2) -> pd.DataFrame:
    """prices_daily 적재용: 일봉 + 거래대금 + 시총 + 외국인/기관 순매수. 연 단위로 나눠 조회한다.
    반환: 인덱스=날짜(date), 컬럼 open high low close volume value market_cap foreign_net inst_net
    per pbr div_yield."""
    frames = []
    for s, e in _year_windows(start, end):
        base = daily_ohlcv(code, s, e)
        if base.empty:
            continue
        cap = _retry(_stock().get_market_cap_by_date, s, e, code)[["시가총액", "거래대금"]]
        cap = cap.rename(columns={"시가총액": "market_cap", "거래대금": "value"})
        frames.append(
            base.join(cap, how="left")
            .join(investor_net(code, s, e), how="left")
            .join(valuation(code, s, e), how="left")
        )
        time.sleep(pause)
    if not frames:
        return pd.DataFrame(
            columns=[
                "open",
                "high",
                "low",
                "close",
                "volume",
                "value",
                "market_cap",
                "foreign_net",
                "inst_net",
                "per",
                "pbr",
                "div_yield",
            ]
        )
    out = pd.concat(frames)
    out.index = pd.to_datetime(out.index).date
    return out[~out.index.duplicated(keep="last")]


def index_close(index_ticker: str, start: str, end: str, pause: float = 0.2) -> pd.Series:
    """지수 종가 (인덱스=날짜(date))."""
    parts = []
    for s, e in _year_windows(start, end):
        df = _retry(_stock().get_index_ohlcv_by_date, s, e, index_ticker)
        if not df.empty:
            parts.append(df["종가"])
        time.sleep(pause)
    if not parts:
        return pd.Series(dtype=float)
    out = pd.concat(parts)
    out.index = pd.to_datetime(out.index).date
    return out[~out.index.duplicated(keep="last")]


def trading_days(start: str, end: str) -> list[date]:
    """거래일 목록 (코스피 지수가 존재하는 날)."""
    return sorted(index_close(KOSPI_INDEX, start, end, pause=0.0).index)
