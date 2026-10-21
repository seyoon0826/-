"""predictions, prediction_factors, prediction_news, prediction_scenarios, prediction_outcomes,
model_metrics (SPEC 9.3)
"""

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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(ForeignKey("stocks.code"))
    as_of_date: Mapped[date] = mapped_column(Date)
    horizon: Mapped[int] = mapped_column(SmallInteger)
    close_at_asof: Mapped[int] = mapped_column(Integer)
    p_up: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    p_outperform: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    q10: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    q50: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    q90: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    model_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("code", "as_of_date", "horizon", "model_version", name="uq_predictions_key"),
        CheckConstraint("p_up >= 0 AND p_up <= 1", name="ck_predictions_p_up"),
        CheckConstraint("q10 <= q50 AND q50 <= q90", name="ck_predictions_quantiles_monotonic"),  # FR-PR-3
        Index("ix_predictions_as_of_date", "as_of_date"),
    )


class PredictionFactor(Base):
    __tablename__ = "prediction_factors"

    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("predictions.id", ondelete="CASCADE"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    feature: Mapped[str] = mapped_column(String(64))
    value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    shap_value: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    label: Mapped[str | None] = mapped_column(Text)


class PredictionNews(Base):
    __tablename__ = "prediction_news"

    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("predictions.id", ondelete="CASCADE"), primary_key=True
    )
    news_id: Mapped[int] = mapped_column(ForeignKey("news.id", ondelete="CASCADE"), primary_key=True)


class PredictionScenario(Base):
    __tablename__ = "prediction_scenarios"

    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("predictions.id", ondelete="CASCADE"), primary_key=True
    )
    summary: Mapped[str] = mapped_column(Text)
    bull: Mapped[str] = mapped_column(Text)
    base: Mapped[str] = mapped_column(Text)
    bear: Mapped[str] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(16))  # llm | template
    llm_model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("generated_by IN ('llm','template')", name="ck_prediction_scenarios_generated_by"),
    )


class PredictionOutcome(Base):
    __tablename__ = "prediction_outcomes"

    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("predictions.id", ondelete="CASCADE"), primary_key=True
    )
    target_date: Mapped[date] = mapped_column(Date)
    actual_return: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    actual_excess: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    hit_direction: Mapped[bool] = mapped_column(Boolean)
    in_range: Mapped[bool] = mapped_column(Boolean)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelMetric(Base):
    __tablename__ = "model_metrics"

    model_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(8), primary_key=True)  # offline | live
    window: Mapped[str] = mapped_column(String(8), primary_key=True)  # 20 | 60 | 250 | all
    strategy: Mapped[str] = mapped_column(
        String(16), primary_key=True
    )  # model | always_up | momentum | kospi
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    n: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("scope IN ('offline','live')", name="ck_model_metrics_scope"),
        CheckConstraint("\"window\" IN ('20','60','250','all')", name="ck_model_metrics_window"),
        CheckConstraint(
            "strategy IN ('model','always_up','momentum','kospi')", name="ck_model_metrics_strategy"
        ),
    )
