"""pykrx: 키 불필요. 일봉, 시총, 투자자별 수급."""
from datetime import datetime, timedelta
import pandas as pd
from pykrx import stock


def latest_business_day() -> str:
    """오늘 기준 가장 최근 영업일(YYYYMMDD)."""
    d = datetime.now()
    for _ in range(10):
        s = d.strftime("%Y%m%d")
        if not stock.get_market_ohlcv_by_ticker(s, market="KOSPI").empty:
            return s
        d -= timedelta(days=1)
    raise RuntimeError("영업일을 찾지 못했습니다")


def top_marketcap(n: int = 30, date: str | None = None) -> pd.DataFrame:
    """코스피 시총 상위 n종목 (code, name, 시가총액)."""
    date = date or latest_business_day()
    df = stock.get_market_cap_by_ticker(date, market="KOSPI")
    df = df.sort_values("시가총액", ascending=False).head(n)
    df["name"] = [stock.get_market_ticker_name(t) for t in df.index]
    return df.reset_index().rename(columns={"티커": "code"})[["code", "name", "시가총액"]]


def daily_ohlcv(code: str, start: str, end: str) -> pd.DataFrame:
    """일봉. start/end 는 'YYYYMMDD'."""
    df = stock.get_market_ohlcv_by_date(start, end, code)
    return df.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                              "종가": "close", "거래량": "volume"})[
        ["open", "high", "low", "close", "volume"]]


def investor_net(code: str, start: str, end: str) -> pd.DataFrame:
    """투자자별 순매수 금액(외국인, 기관)."""
    df = stock.get_market_trading_value_by_date(start, end, code)
    return df.rename(columns={"외국인합계": "foreign_net", "기관합계": "inst_net"})[
        ["foreign_net", "inst_net"]]


def collect(code: str, start: str, end: str) -> pd.DataFrame:
    """일봉 + 수급 합치기 (학습/DB 적재용)."""
    out = daily_ohlcv(code, start, end).join(investor_net(code, start, end))
    out.insert(0, "code", code)
    return out
