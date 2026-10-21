import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
"""키 발급 후 각 API가 되는지 확인하는 스모크 테스트."""
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

def run(name, fn):
    try:
        print(f"[OK]   {name}:", fn())
    except Exception as e:
        print(f"[FAIL] {name}: {e}")

from app.clients import pykrx_client as pk, dart_client as dart, naver_news_client as nv
from app.clients import ecos_client as ecos, kis_client as kis

run("pykrx", lambda: pk.daily_ohlcv("005930", "20250101", "20250110").tail(2).to_dict())
run("DART", lambda: len(dart.load_corp_codes()))
run("Naver", lambda: nv.search_news("삼성전자", display=3)[0]["title"])
run("ECOS", lambda: ecos.base_rate("20250101", "20250131")[-1])
run("KIS", lambda: kis.current_price("005930")["stck_prpr"])
