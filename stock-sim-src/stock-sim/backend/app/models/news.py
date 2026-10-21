"""news, news_stock_map, news_analysis, disclosures (SPEC 9.2)"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class News(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, unique=True)  # 정규화된 URL
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_news_published_at", "published_at"),)


class NewsStockMap(Base):
    __tablename__ = "news_stock_map"

    news_id: Mapped[int] = mapped_column(ForeignKey("news.id", ondelete="CASCADE"), primary_key=True)
    code: Mapped[str] = mapped_column(ForeignKey("stocks.code"), primary_key=True)
    relevance: Mapped[Decimal] = mapped_column(Numeric(3, 2))

    __table_args__ = (
        CheckConstraint("relevance >= 0 AND relevance <= 1", name="ck_news_stock_map_relevance"),
        Index("ix_news_stock_map_code", "code"),
    )


class NewsAnalysis(Base):
    __tablename__ = "news_analysis"

    news_id: Mapped[int] = mapped_column(ForeignKey("news.id", ondelete="CASCADE"), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    sentiment: Mapped[str] = mapped_column(String(16))
    sentiment_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    event_type: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))


class Disclosure(Base):
    __tablename__ = "disclosures"

    rcept_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    code: Mapped[str] = mapped_column(ForeignKey("stocks.code"))
    corp_code: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(Text)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    report_type: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_disclosures_code_filed_at", "code", "filed_at"),)
