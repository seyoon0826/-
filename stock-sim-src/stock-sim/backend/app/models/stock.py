"""stocks, prices_daily, index_daily, macro_daily, market_calendar (SPEC 9.1)"""

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Stock(Base):
    __tablename__ = "stocks"

    code: Mapped[str] = mapped_column(String(6), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    market: Mapped[str] = mapped_column(String(16), server_default="KOSPI")
    sector: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PriceDaily(Base):
    """TimescaleDB hypertable on `date` (마이그레이션에서 create_hypertable)."""

    __tablename__ = "prices_daily"

    code: Mapped[str] = mapped_column(ForeignKey("stocks.code"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[int] = mapped_column(Integer)
    high: Mapped[int] = mapped_column(Integer)
    low: Mapped[int] = mapped_column(Integer)
    close: Mapped[int] = mapped_column(Integer)
    volume: Mapped[int] = mapped_column(BigInteger)
    value: Mapped[int | None] = mapped_column(BigInteger)
    foreign_net: Mapped[int | None] = mapped_column(BigInteger)
    inst_net: Mapped[int | None] = mapped_column(BigInteger)
    market_cap: Mapped[int | None] = mapped_column(BigInteger)


class IndexDaily(Base):
    __tablename__ = "index_daily"

    index_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[float] = mapped_column(Numeric(14, 4))


class MacroDaily(Base):
    __tablename__ = "macro_daily"

    series: Mapped[str] = mapped_column(String(32), primary_key=True)  # base_rate | usdkrw
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Numeric(18, 6))


class MarketCalendar(Base):
    __tablename__ = "market_calendar"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean)
