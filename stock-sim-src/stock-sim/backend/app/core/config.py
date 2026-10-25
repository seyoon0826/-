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

    # 모의투자 (SPEC 7, 15)
    paper_ignore_market_hours: bool = False  # 개발용: 장 시간 무시
    paper_max_order_qty: int = 100_000
    paper_initial_cash_options: tuple[int, ...] = (10_000_000, 30_000_000, 50_000_000, 100_000_000)

    # AI 계정 규칙 (FR-AI-2)
    ai_top_n: int = 5
    ai_buy_threshold: float = 0.55
    ai_sell_threshold: float = 0.50
    ai_mode: Literal["rank", "probability"] = "rank"  # rank: 순위 상위 N 매수, AI_SELL_RANK 밖이면 매도
    ai_sell_rank: int = 10

    # 인증 (SPEC 6.8, FR-AU)
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    refresh_cookie_secure: bool = False  # HTTPS 배포(도메인 연결) 시 true
    login_rate_per_min: int = 10  # IP 당 분당 로그인/가입 시도 (FR-AU-5)
    cors_origins: str = "http://localhost:3000"  # 쉼표 구분 화이트리스트 (SPEC 14)

    # 소셜 로그인 (카카오, 구글). 키가 비어 있으면 그 제공자 버튼을 감춘다.
    kakao_rest_api_key: str = ""  # 카카오 디벨로퍼스 > 앱 키 > REST API 키 (= client_id)
    kakao_client_secret: str = ""  # 카카오 로그인 > 보안 > Client Secret (기본 사용 설정)
    google_client_id: str = ""
    google_client_secret: str = ""
    api_public_url: str = (
        "http://localhost:8000"  # 콜백 주소: {api_public_url}/api/v1/auth/oauth/{provider}/callback
    )
    frontend_url: str = "http://localhost:3000"  # 로그인 후 돌아갈 프론트 주소

    # 예측 모델 (SPEC 8.4, 15). MODEL_VERSION 이 비어 있으면 모델 없이 기동한다(예측 API 는 Phase 6).
    model_version: str = ""
    model_dir: str = "../ml/models"
    # 시나리오 생성 LLM (SPEC 6.4, Q4). 모델명·예산은 오너 결정 사항 — 키가 없거나 비우면 템플릿 문장.
    llm_model: str = "claude-opus-5-5"


settings = Settings()
