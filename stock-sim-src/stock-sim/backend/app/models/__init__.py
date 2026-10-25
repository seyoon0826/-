"""모든 모델을 import 해서 Base.metadata 에 등록한다 (Alembic autogenerate 용)."""

from app.models.news import Disclosure, News, NewsAnalysis, NewsStockMap
from app.models.paper import (
    AdminAudit,
    CashLedger,
    FeeConfig,
    JobRun,
    OAuthAccount,
    Order,
    PaperAccount,
    PortfolioSnapshot,
    Position,
    Trade,
    User,
)
from app.models.prediction import (
    ModelMetric,
    Prediction,
    PredictionFactor,
    PredictionNews,
    PredictionOutcome,
    PredictionScenario,
)
from app.models.stock import IndexDaily, MacroDaily, MarketCalendar, PriceDaily, Stock

__all__ = [
    "AdminAudit",
    "CashLedger",
    "Disclosure",
    "FeeConfig",
    "IndexDaily",
    "JobRun",
    "MacroDaily",
    "MarketCalendar",
    "ModelMetric",
    "News",
    "NewsAnalysis",
    "NewsStockMap",
    "Order",
    "PaperAccount",
    "PortfolioSnapshot",
    "Position",
    "Prediction",
    "PredictionFactor",
    "PredictionNews",
    "PredictionOutcome",
    "PredictionScenario",
    "PriceDaily",
    "Stock",
    "Trade",
    "OAuthAccount",
    "User",
]
