from dotenv import load_dotenv

load_dotenv("../.env")  # app/clients 가 os.getenv 로 키를 읽으므로 먼저 로드

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import stocks, predictions, performance, paper, ws

app = FastAPI(title="StockSim API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(stocks.router, prefix="/stocks", tags=["stocks"])
app.include_router(predictions.router, prefix="/stocks", tags=["prediction"])
app.include_router(performance.router, prefix="/model", tags=["performance"])
app.include_router(paper.router, prefix="/paper", tags=["paper"])
app.include_router(ws.router, tags=["ws"])


@app.get("/health")
def health():
    return {"ok": True}
