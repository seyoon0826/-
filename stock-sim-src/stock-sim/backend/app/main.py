from dotenv import load_dotenv

load_dotenv("../.env")  # app/clients 가 os.getenv 로 키를 읽으므로 먼저 로드

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.api import paper, performance, predictions, stocks, ws  # noqa: E402
from app.api.common import ApiError, api_error_handler  # noqa: E402

API_PREFIX = "/api/v1"

app = FastAPI(title="StockSim API", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"], allow_headers=["*"]
)
app.add_exception_handler(ApiError, api_error_handler)

app.include_router(stocks.router, prefix=f"{API_PREFIX}/stocks", tags=["stocks"])
app.include_router(predictions.router, prefix=f"{API_PREFIX}/stocks", tags=["prediction"])
app.include_router(performance.router, prefix=f"{API_PREFIX}/model", tags=["performance"])
app.include_router(paper.router, prefix=f"{API_PREFIX}/paper", tags=["paper"])
app.include_router(ws.router, prefix=API_PREFIX, tags=["ws"])


@app.get("/health")
def health():
    return {"ok": True}
