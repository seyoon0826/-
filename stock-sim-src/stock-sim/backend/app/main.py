from dotenv import load_dotenv

load_dotenv("../.env")  # app/clients 가 os.getenv 로 키를 읽으므로 먼저 로드

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.api import auth, paper, performance, predictions, stocks, ws  # noqa: E402
from app.api.common import ApiError, api_error_handler  # noqa: E402
from app.core.config import settings  # noqa: E402

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """MODEL_VERSION 이 설정돼 있으면 모델을 로드한다.
    features.json 이 서빙 피처와 다르면 기동 실패 (SPEC 8.4)."""
    app.state.model = None
    if settings.model_version:
        from app.ml.infer import ModelBundle

        app.state.model = ModelBundle.load(settings.model_dir, settings.model_version)
    yield


app = FastAPI(title="StockSim API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip() for o in settings.cors_origins.split(",") if o.strip()
    ],  # 화이트리스트 (SPEC 14)
    allow_credentials=True,  # 리프레시 쿠키
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(ApiError, api_error_handler)

app.include_router(auth.router, prefix=f"{API_PREFIX}/auth", tags=["auth"])
app.include_router(stocks.router, prefix=f"{API_PREFIX}/stocks", tags=["stocks"])
app.include_router(predictions.router, prefix=f"{API_PREFIX}/stocks", tags=["prediction"])
app.include_router(performance.router, prefix=f"{API_PREFIX}/model", tags=["performance"])
app.include_router(paper.router, prefix=f"{API_PREFIX}/paper", tags=["paper"])
app.include_router(ws.router, prefix=API_PREFIX, tags=["ws"])


@app.get("/health")
def health():
    return {"ok": True}
