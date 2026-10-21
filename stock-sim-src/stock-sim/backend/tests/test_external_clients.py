"""외부 API 클라이언트 (SPEC 5): 재시도/백오프, 4xx 무재시도, 키 비노출, 페이징. 실제 네트워크 없음."""

import io
import zipfile

import pytest
import requests

from app.clients import dart_client, ecos_client, naver_news_client
from app.clients import http as http_mod
from app.clients.http import RateLimiter, UpstreamError, request


class Resp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._body, self.content = status, body, content

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Fake:
    """requests.request 대체: 응답/예외를 순서대로 돌려주고 호출을 기록한다."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    for mod in (dart_client, ecos_client, naver_news_client):
        monkeypatch.setattr(mod._limiter, "min_interval", 0)
    monkeypatch.setenv("DART_API_KEY", "DARTKEY")
    monkeypatch.setenv("ECOS_API_KEY", "ECOSKEY")
    monkeypatch.setenv("NAVER_CLIENT_ID", "NID")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "NSECRET")


def patch(monkeypatch, *responses) -> Fake:
    fake = Fake(*responses)
    monkeypatch.setattr(requests, "request", fake)
    return fake


# ---------- http.request ----------
def test_retry_backoff_1_2_4_then_give_up(monkeypatch):
    fake, sleeps = patch(monkeypatch, requests.ConnectionError("x")), []
    with pytest.raises(UpstreamError, match="재시도 소진"):
        request("GET", "https://x.example/a?key=SECRET", sleep=sleeps.append)
    assert sleeps == [1, 2, 4] and len(fake.calls) == 4


def test_retry_succeeds_midway(monkeypatch):
    fake, sleeps = patch(monkeypatch, Resp(503), Resp(200, {"a": 1})), []
    assert request("GET", "https://x.example", sleep=sleeps.append).json() == {"a": 1}
    assert sleeps == [1] and len(fake.calls) == 2


def test_4xx_not_retried_but_429_is(monkeypatch):
    fake = patch(monkeypatch, Resp(401))
    with pytest.raises(UpstreamError, match="HTTP 401"):
        request("GET", "https://x.example", sleep=lambda s: None)
    assert len(fake.calls) == 1
    fake = patch(monkeypatch, Resp(429))
    with pytest.raises(UpstreamError):
        request("GET", "https://x.example", sleep=lambda s: None)
    assert len(fake.calls) == 4


def test_error_messages_never_contain_url_or_keys(monkeypatch):
    patch(monkeypatch, requests.ConnectionError("https://ecos.example/SECRETKEY/json"))
    with pytest.raises(UpstreamError) as e:
        request("GET", "https://ecos.example/SECRETKEY/json", sleep=lambda s: None, name="ECOS")
    assert "SECRETKEY" not in str(e.value)


def test_timeout_is_15_seconds(monkeypatch):
    fake = patch(monkeypatch, Resp(200, {}))
    request("GET", "https://x.example")
    assert fake.calls[0][2]["timeout"] == 15


def test_rate_limiter_enforces_min_interval():
    t, slept = [0.0], []
    lim = RateLimiter(1.0, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    lim.wait()
    lim.wait()
    assert slept == [1.0]
    assert http_mod.BACKOFF == (1, 2, 4)


# ---------- DART ----------
def _corp_zip():
    xml = (
        "<result><list><corp_code>00126380</corp_code><stock_code>005930</stock_code></list>"
        "<list><corp_code>00164779</corp_code><stock_code>000660</stock_code></list>"
        "<list><corp_code>99999999</corp_code><stock_code> </stock_code></list></result>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


def test_dart_corp_codes_maps_only_listed_companies(monkeypatch):
    patch(monkeypatch, Resp(200, content=_corp_zip()))
    assert dart_client.load_corp_codes() == {"005930": "00126380", "000660": "00164779"}


def test_dart_paginates_until_total_page(monkeypatch):
    p1 = {"status": "000", "total_page": 2, "list": [{"rcept_no": "1"}]}
    p2 = {"status": "000", "total_page": 2, "list": [{"rcept_no": "2"}]}
    fake = patch(monkeypatch, Resp(200, p1), Resp(200, p2))
    assert [x["rcept_no"] for x in dart_client.disclosures("00126380", "20260101", "20261020")] == ["1", "2"]
    assert [c[2]["params"]["page_no"] for c in fake.calls] == [1, 2]
    assert fake.calls[0][2]["params"]["crtfc_key"] == "DARTKEY"


def test_dart_no_data_is_empty_and_limit_exceeded_raises(monkeypatch):
    patch(monkeypatch, Resp(200, {"status": "013", "message": "조회된 데이터가 없습니다."}))
    assert dart_client.disclosures("1", "20260101", "20261020") == []
    patch(monkeypatch, Resp(200, {"status": "020", "message": "한도 초과"}))
    with pytest.raises(UpstreamError, match="020"):
        dart_client.disclosures("1", "20260101", "20261020")


def test_clients_require_keys(monkeypatch):
    monkeypatch.delenv("DART_API_KEY")
    monkeypatch.delenv("NAVER_CLIENT_ID")
    monkeypatch.delenv("ECOS_API_KEY")
    with pytest.raises(UpstreamError):
        dart_client.disclosures("1", "20260101", "20261020")
    with pytest.raises(UpstreamError):
        naver_news_client.search_news("삼성전자")
    with pytest.raises(UpstreamError):
        ecos_client.base_rate("20260101", "20260131")


# ---------- Naver ----------
def test_naver_sends_headers_and_clamps_window(monkeypatch):
    fake = patch(monkeypatch, Resp(200, {"items": [{"title": "t"}]}))
    assert naver_news_client.search_news("삼성전자", display=100, start=951) == [{"title": "t"}]
    kw = fake.calls[0][2]
    assert (
        kw["headers"]["X-Naver-Client-Id"] == "NID" and kw["params"]["display"] == 50
    )  # start+display-1 <= 1000
    assert kw["params"]["sort"] == "date"


def test_naver_beyond_start_limit_makes_no_call(monkeypatch):
    fake = patch(monkeypatch, Resp(200, {"items": []}))
    assert naver_news_client.search_news("x", start=1001) == []
    assert fake.calls == []


# ---------- ECOS ----------
def test_ecos_pages_by_1000_and_stops(monkeypatch):
    def page(n):
        return {
            "StatisticSearch": {
                "list_total_count": 1500,
                "row": [{"TIME": "20260101", "DATA_VALUE": "1"}] * n,
            }
        }

    fake = patch(monkeypatch, Resp(200, page(1000)), Resp(200, page(500)))
    assert len(ecos_client.usdkrw("20260101", "20261231")) == 1500
    assert "/1/1000/731Y001/D/20260101/20261231/0000001" in fake.calls[0][1]
    assert "/1001/2000/" in fake.calls[1][1]


def test_ecos_no_data_empty_and_other_errors_raise_without_key(monkeypatch):
    patch(monkeypatch, Resp(200, {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}))
    assert ecos_client.base_rate("20260101", "20260131") == []
    patch(monkeypatch, Resp(200, {"RESULT": {"CODE": "ERROR-500", "MESSAGE": "서버 오류"}}))
    with pytest.raises(UpstreamError) as e:
        ecos_client.base_rate("20260101", "20260131")
    assert "ECOSKEY" not in str(e.value)
