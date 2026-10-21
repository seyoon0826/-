"""DART 공시 (OpenDART). 키: opendart.fss.or.kr

TODO(verify): 공식 개발가이드 대조 필요 — list.json 응답 필드(rcept_no, corp_code, report_nm, rcept_dt,
total_page), page_count 최대값, 상태코드(000 정상, 013 조회 결과 없음, 020 요청 제한 초과).
서드파티 자료로만 확인했고 실제 키로는 호출해 보지 못했다.
"""

from __future__ import annotations

import io
import os
import xml.etree.ElementTree as ET
import zipfile

from app.clients.http import RateLimiter, UpstreamError, request, request_json

BASE = "https://opendart.fss.or.kr/api"
PAGE_COUNT = 100
_limiter = RateLimiter(0.2)  # 분당 100회 초과 시 제한된다는 안내 기준  # TODO(verify)


def _key() -> str:
    key = os.getenv("DART_API_KEY", "")
    if not key:
        raise UpstreamError("DART_API_KEY 가 설정되지 않았습니다")
    return key


def load_corp_codes() -> dict[str, str]:
    """종목코드(6자리) -> DART corp_code(8자리) 매핑. 응답은 zip 안의 XML."""
    r = request("GET", f"{BASE}/corpCode.xml", params={"crtfc_key": _key()}, limiter=_limiter, name="DART")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        root = ET.fromstring(z.read(z.namelist()[0]))
    mapping = {}
    for item in root.iter("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        if stock_code:
            mapping[stock_code] = (item.findtext("corp_code") or "").strip()
    return mapping


def disclosures(corp_code: str, start: str, end: str, page_count: int = PAGE_COUNT) -> list[dict]:
    """공시 목록 전체(페이지 순회). start/end 는 'YYYYMMDD'."""
    out: list[dict] = []
    page = 1
    while True:
        data = request_json(
            "GET",
            f"{BASE}/list.json",
            params={
                "crtfc_key": _key(),
                "corp_code": corp_code,
                "bgn_de": start,
                "end_de": end,
                "page_no": page,
                "page_count": page_count,
            },
            limiter=_limiter,
            name="DART",
        )
        status = data.get("status")
        if status == "013":  # 조회 결과 없음
            break
        if status != "000":
            raise UpstreamError(f"DART 오류: {status} {data.get('message')}")  # 020 = 요청 제한 초과
        out.extend(data.get("list", []))
        if page >= int(data.get("total_page", 1)):
            break
        page += 1
    return out
