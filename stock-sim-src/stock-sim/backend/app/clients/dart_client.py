"""DART 공시. 키: opendart.fss.or.kr"""
import io, os, zipfile
import xml.etree.ElementTree as ET
import requests

BASE = "https://opendart.fss.or.kr/api"
KEY = os.getenv("DART_API_KEY")


def load_corp_codes() -> dict[str, str]:
    """종목코드(6자리) -> DART corp_code(8자리) 매핑."""
    r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": KEY}, timeout=30)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        root = ET.fromstring(z.read(z.namelist()[0]))
    m = {}
    for item in root.iter("list"):
        sc = (item.findtext("stock_code") or "").strip()
        if sc:
            m[sc] = item.findtext("corp_code")
    return m


def disclosures(corp_code: str, start: str, end: str, page_count: int = 100) -> list[dict]:
    """공시 목록. start/end 는 'YYYYMMDD'."""
    r = requests.get(f"{BASE}/list.json", params={
        "crtfc_key": KEY, "corp_code": corp_code,
        "bgn_de": start, "end_de": end, "page_count": page_count,
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("status") not in ("000", "013"):  # 013: 조회 결과 없음
        raise RuntimeError(f"DART 오류: {data.get('status')} {data.get('message')}")
    return data.get("list", [])
