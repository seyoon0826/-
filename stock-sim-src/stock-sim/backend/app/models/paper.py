"""users, paper_accounts, orders, trades, positions, cash_ledger, portfolio_snapshots, fee_config, job_runs
(SPEC 9.4, 9.5). 금액은 정수(원) 또는 Numeric — float 금지."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(8))  # USER | AI
    initial_cash: Mapped[int] = mapped_column(BigInteger)
    cash: Mapped[int] = mapped_column(BigInteger)
    reserved_cash: Mapped[int] = mapped_column(BigInteger, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("reserved_cash >= 0 AND reserved_cash <= cash", name="ck_paper_accounts_reserved"),
        CheckConstraint("type IN ('USER','AI')", name="ck_paper_accounts_type"),
        CheckConstraint("initial_cash > 0", name="ck_paper_accounts_initial_cash"),
        # FR-PT-1 / FR-AI-1: 사용자당 (열린) USER 계좌 1개, AI 계좌 1개
        Index(
            "uq_paper_accounts_open_per_user_type",
            "user_id",
            "type",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"))
    code: Mapped[str] = mapped_column(ForeignKey("stocks.code"))
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    order_type: Mapped[str] = mapped_column(String(6))  # MARKET | LIMIT
    qty: Mapped[int] = mapped_column(Integer)  # 검증 실패(INVALID_QTY) 주문도 저장하므로 양수 CHECK 없음
    filled_qty: Mapped[int] = mapped_column(Integer, server_default="0")
    limit_price: Mapped[int | None] = mapped_column(Integer)
    reserved_amount: Mapped[int] = mapped_column(BigInteger, server_default="0")
    status: Mapped[str] = mapped_column(String(10))
    reject_reason: Mapped[str | None] = mapped_column(String(32))
    queued: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    source: Mapped[str] = mapped_column(String(4), server_default="USER")  # USER | AI
    prediction_id: Mapped[int | None] = mapped_column(ForeignKey("predictions.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key", name="uq_orders_account_idempotency"),
        CheckConstraint("side IN ('BUY','SELL')", name="ck_orders_side"),
        CheckConstraint("order_type IN ('MARKET','LIMIT')", name="ck_orders_order_type"),
        CheckConstraint(
            "status IN ('PENDING','PARTIAL','FILLED','CANCELED','EXPIRED','REJECTED')",
            name="ck_orders_status",
        ),
        CheckConstraint("source IN ('USER','AI')", name="ck_orders_source"),
        CheckConstraint(
            "filled_qty >= 0 AND (filled_qty = 0 OR filled_qty <= qty)", name="ck_orders_filled_qty"
        ),
        CheckConstraint("reserved_amount >= 0", name="ck_orders_reserved_amount"),
        Index("ix_orders_status_code", "status", "code"),
        Index("ix_orders_account_created", "account_id", "created_at"),
    )


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"))
    code: Mapped[str] = mapped_column(ForeignKey("stocks.code"))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(BigInteger)
    fee: Mapped[int] = mapped_column(BigInteger, server_default="0")
    tax: Mapped[int] = mapped_column(BigInteger, server_default="0")
    event_seq: Mapped[int] = mapped_column(BigInteger)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("order_id", "event_seq", name="uq_trades_order_event_seq"),  # 체결 멱등 키 (7.9)
        CheckConstraint("side IN ('BUY','SELL')", name="ck_trades_side"),
        CheckConstraint("qty > 0 AND price > 0", name="ck_trades_positive"),
        CheckConstraint("fee >= 0 AND tax >= 0", name="ck_trades_costs"),
        Index("ix_trades_account_filled", "account_id", "filled_at"),
    )


class Position(Base):
    __tablename__ = "positions"

    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), primary_key=True)
    code: Mapped[str] = mapped_column(ForeignKey("stocks.code"), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, server_default="0")
    reserved_qty: Mapped[int] = mapped_column(Integer, server_default="0")
    avg_price: Mapped[Decimal] = mapped_column(Numeric(16, 2), server_default="0")
    realized_pnl: Mapped[int] = mapped_column(BigInteger, server_default="0")

    __table_args__ = (
        CheckConstraint("reserved_qty >= 0 AND reserved_qty <= qty", name="ck_positions_reserved_qty"),
    )


class CashLedger(Base):
    """잔고 변동의 유일한 근거 (7.8). amount 는 부호 포함."""

    __tablename__ = "cash_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"))
    type: Mapped[str] = mapped_column(String(8))
    affects: Mapped[str] = mapped_column(String(8))  # cash | reserved
    amount: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    ref_type: Mapped[str | None] = mapped_column(String(16))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "type IN ('DEPOSIT','BUY','SELL','FEE','TAX','RESERVE','RELEASE')", name="ck_cash_ledger_type"
        ),
        CheckConstraint("affects IN ('cash','reserved')", name="ck_cash_ledger_affects"),
        CheckConstraint(
            "(type IN ('RESERVE','RELEASE')) = (affects = 'reserved')", name="ck_cash_ledger_type_affects"
        ),
        Index("ix_cash_ledger_account_id", "account_id", "id"),
    )


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    cash: Mapped[int] = mapped_column(BigInteger)
    stock_value: Mapped[int] = mapped_column(BigInteger)
    total_value: Mapped[int] = mapped_column(BigInteger)
    return_pct: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    kospi_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))


class FeeConfig(Base):
    """수수료/세율. 하드코딩 금지 — 시드 값은 오너 확인 전까지 비워 둔다 (SPEC Q1)."""

    __tablename__ = "fee_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    effective_from: Mapped[date] = mapped_column(Date, unique=True)
    buy_fee_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    sell_fee_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    sell_tax_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "buy_fee_rate >= 0 AND sell_fee_rate >= 0 AND sell_tax_rate >= 0", name="ck_fee_config_rates"
        ),
    )


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # running | success | partial | failed
    items_ok: Mapped[int] = mapped_column(Integer, server_default="0")
    items_failed: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('running','success','partial','failed')", name="ck_job_runs_status"),
        Index("ix_job_runs_job_started", "job", "started_at"),
    )
