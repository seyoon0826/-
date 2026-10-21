"""한국은행 ECOS. 키: ecos.bok.or.kr"""
import os
import requests

BASE = "https://ecos.bok.or.kr/api/StatisticSearch"


def series(stat_code: str, cycle: str, start: str, end: str, item_code: str,
           limit: int = 1000) -> list[dict]:
    """cycle: D(일) M(월) Q(분기) A(연). start/end 형식은 cycle에 맞춤 (D=YYYYMMDD, M=YYYYMM)."""
    key = os.getenv("ECOS_API_KEY")
    url = f"{BASE}/{key}/json/kr/1/{limit}/{stat_code}/{cycle}/{start}/{end}/{item_code}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "StatisticSearch" not in data:
        raise RuntimeError(f"ECOS 오류: {data}")
    return data["StatisticSearch"]["row"]  # TIME, DATA_VALUE 등


def base_rate(start: str, end: str):       # 기준금리 (일 단위)
    return series("722Y001", "D", start, end, "0101000")


def usdkrw(start: str, end: str):          # 원/달러 환율 (일 단위)
    return series("731Y001", "D", start, end, "0000001")
