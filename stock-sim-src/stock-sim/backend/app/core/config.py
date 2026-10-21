from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://stock:stock@localhost:5432/stocksim"
    redis_url: str = "redis://localhost:6379/0"
    anthropic_api_key: str = ""

    # 시세 계층 (SPEC 5.1, 15)
    market_provider: Literal["pykrx", "replay", "kis"] = "pykrx"
    replay_speed: float = 60.0  # 재생 배속 (0 이하이면 대기 없이 즉시 재생)
    eod_orderbook_qty: int = 10_000  # PykrxEodProvider 합성 호가 잔량
    quote_stale_sec: int = 30  # 이보다 오래된 시세로는 시장가 주문 거부 (SPEC 14)

    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_paper: bool = True
    # 실시간 등록 한도(체결+호가 TR 합산). 공식 샘플 러너는 40건. 문서로 재확인 필요.  # TODO(verify)
    kis_max_subscriptions: int = 40
    kis_poll_interval_sec: float = 3.0  # 웹소켓 장애 시 REST 폴링 주기
    kis_ws_silence_sec: float = 30.0  # 이 시간 동안 메시지가 없으면 REST 폴링으로 강등

    # 수집 파이프라인 (SPEC 5, 6.2, 12)
    universe_size: int = 30
    universe_include_preferred: bool = False  # 우선주 포함 여부. 오너 미결정 — 기본은 보통주만  # TODO(owner)
    news_interval_open_min: int = 5  # 장중(08:30~16:00)
    news_interval_closed_min: int = 30
    disclosure_interval_open_min: int = 10
    disclosure_interval_closed_min: int = 60
    cache_dir: str = ".cache"  # DART corp_code 등 주 1회 갱신 캐시


settings = Settings()
