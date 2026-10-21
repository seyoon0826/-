"""Phase 1: 모델/마이그레이션 CRUD 와 제약 테스트 (SPEC 9장)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.core.db import Base
from app.models import (
    CashLedger,
    Disclosure,
    FeeConfig,
    JobRun,
    ModelMetric,
    News,
    NewsStockMap,
    Order,
    PaperAccount,
    Position,
    Prediction,
    PredictionFactor,
    PredictionOutcome,
    PredictionScenario,
    PriceDaily,
    Stock,
    Trade,
    User,
)

NOW = datetime(2026, 10, 20, 15, 40, tzinfo=UTC)


def _stock(db, code="005930"):
    s = Stock(code=code, name="삼성전자", aliases=["삼성", "Samsung"])
    db.add(s)
    db.flush()
    return s


def _account(db, cash=10_000_000, type_="USER", email="a@example.com"):
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, password_hash="x")
        db.add(user)
        db.flush()
    acct = PaperAccount(user_id=user.id, type=type_, initial_cash=cash, cash=cash)
    db.add(acct)
    db.flush()
    return acct


def _prediction(db, code="005930", **kw):
    vals = dict(
        code=code,
        as_of_date=date(2026, 10, 20),
        horizon=5,
        close_at_asof=72100,
        p_up=Decimal("0.58"),
        p_outperform=Decimal("0.53"),
        q10=Decimal("-0.021"),
        q50=Decimal("0.009"),
        q90=Decimal("0.043"),
        model_version="lgbm-20261015-a1b2c3",
    )
    vals.update(kw)
    p = Prediction(**vals)
    db.add(p)
    db.flush()
    return p


def test_all_spec_tables_exist(engine):
    expected = {
        "stocks",
        "prices_daily",
        "index_daily",
        "macro_daily",
        "market_calendar",
        "news",
        "news_stock_map",
        "news_analysis",
        "disclosures",
        "predictions",
        "prediction_factors",
        "prediction_news",
        "prediction_scenarios",
        "prediction_outcomes",
        "model_metrics",
        "users",
        "paper_accounts",
        "orders",
        "trades",
        "positions",
        "cash_ledger",
        "portfolio_snapshots",
        "fee_config",
        "job_runs",
    }
    assert expected <= set(inspect(engine).get_table_names())
    assert expected <= set(Base.metadata.tables)


def test_prices_daily_is_hypertable(db):
    n = db.execute(
        text(
            "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name = 'prices_daily'"
        )
    ).scalar()
    assert n == 1


def test_stock_and_price_crud_and_unique(db):
    _stock(db)
    db.add(
        PriceDaily(
            code="005930",
            date=date(2026, 10, 20),
            open=71000,
            high=72500,
            low=70800,
            close=72100,
            volume=1_000_000,
            foreign_net=5,
            inst_net=-3,
        )
    )
    db.flush()
    got = db.get(PriceDaily, ("005930", date(2026, 10, 20)))
    assert got.close == 72100
    assert db.get(Stock, "005930").aliases == ["삼성", "Samsung"]
    assert db.get(Stock, "005930").active is True

    with pytest.raises(IntegrityError), db.begin_nested():  # FR-CL-1: (code, date) 중복 불가
        db.execute(
            insert(PriceDaily).values(
                code="005930", date=date(2026, 10, 20), open=1, high=1, low=1, close=1, volume=1
            )
        )


def test_news_url_unique_and_relevance_range(db):
    _stock(db)
    n = News(url="https://n.example/1", title="t", published_at=NOW)
    db.add(n)
    db.flush()
    db.add(NewsStockMap(news_id=n.id, code="005930", relevance=Decimal("0.6")))
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(News(url="https://n.example/1", title="dup", published_at=NOW))
        db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(NewsStockMap(news_id=n.id, code="005930", relevance=Decimal("1.5")))
        db.flush()


def test_disclosure_crud(db):
    _stock(db)
    db.add(
        Disclosure(rcept_no="20261020000001", code="005930", corp_code="00126380", title="공시", filed_at=NOW)
    )
    db.flush()
    assert db.get(Disclosure, "20261020000001").corp_code == "00126380"


def test_prediction_unique_key_fr_pr_9(db):
    _stock(db)
    _prediction(db)
    with pytest.raises(IntegrityError), db.begin_nested():
        _prediction(db)
    _prediction(db, model_version="other")  # 모델버전이 다르면 허용


def test_prediction_quantiles_must_be_monotonic_fr_pr_3(db):
    _stock(db)
    with pytest.raises(IntegrityError), db.begin_nested():
        _prediction(db, q10=Decimal("0.05"), q50=Decimal("0.01"), q90=Decimal("0.04"))


def test_prediction_children_cascade(db):
    _stock(db)
    p = _prediction(db)
    db.add_all(
        [
            PredictionFactor(
                prediction_id=p.id,
                rank=1,
                feature="ret_20d",
                value=Decimal("0.083"),
                shap_value=Decimal("0.03"),
                label="최근 20거래일 +8.3% 상승",
            ),
            PredictionScenario(
                prediction_id=p.id, summary="s", bull="b", base="m", bear="r", generated_by="llm"
            ),
            PredictionOutcome(
                prediction_id=p.id,
                target_date=date(2026, 10, 27),
                actual_return=Decimal("0.01"),
                hit_direction=True,
                in_range=True,
            ),
        ]
    )
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(
            PredictionScenario(
                prediction_id=_prediction(db, model_version="v2").id,
                summary="s",
                bull="b",
                base="m",
                bear="r",
                generated_by="gpt",
            )
        )
        db.flush()
    db.delete(p)
    db.flush()
    assert db.get(PredictionFactor, (p.id, 1)) is None


def test_model_metrics_window_is_quoted_reserved_word(db):
    db.add(
        ModelMetric(
            model_version="v",
            scope="live",
            window="60",
            strategy="model",
            metric="brier",
            value=Decimal("0.24"),
            n=100,
        )
    )
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(ModelMetric(model_version="v", scope="live", window="7", strategy="model", metric="x", n=1))
        db.flush()


def test_account_reserved_cash_check(db):
    acct = _account(db)
    acct.reserved_cash = 1_000_000
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        acct.reserved_cash = 20_000_000  # reserved_cash <= cash
        db.flush()
    db.refresh(acct)
    with pytest.raises(IntegrityError), db.begin_nested():
        acct.reserved_cash = -1
        db.flush()


def test_one_open_account_per_user_and_type(db):
    acct = _account(db)
    with pytest.raises(IntegrityError), db.begin_nested():  # FR-PT-1: USER 계좌 1개
        db.add(PaperAccount(user_id=acct.user_id, type="USER", initial_cash=1, cash=1))
        db.flush()
    db.add(PaperAccount(user_id=acct.user_id, type="AI", initial_cash=1, cash=1))  # FR-AI-1
    acct.closed_at = NOW  # 초기화(FR-PT-2) 후에는 새 계좌 허용
    db.flush()
    db.add(PaperAccount(user_id=acct.user_id, type="USER", initial_cash=5, cash=5))
    db.flush()


def test_order_status_side_and_idempotency(db):
    _stock(db)
    acct = _account(db)

    def order(**kw):
        vals = dict(
            account_id=acct.id,
            code="005930",
            side="BUY",
            order_type="LIMIT",
            qty=10,
            limit_price=70000,
            status="PENDING",
        )
        vals.update(kw)
        o = Order(**vals)
        db.add(o)
        db.flush()
        return o

    o = order(idempotency_key="k1")
    assert o.id is not None and o.filled_qty == 0 and o.queued is False and o.source == "USER"
    with pytest.raises(IntegrityError), db.begin_nested():
        order(idempotency_key="k1")  # (account_id, idempotency_key) 유니크
    order()  # 키가 없는 주문은 여러 개 가능
    order()
    with pytest.raises(IntegrityError), db.begin_nested():
        order(status="DONE")
    with pytest.raises(IntegrityError), db.begin_nested():
        order(side="HOLD")
    with pytest.raises(IntegrityError), db.begin_nested():
        order(filled_qty=11)
    order(qty=0, status="REJECTED", reject_reason="INVALID_QTY")  # 검증 실패 주문도 저장 가능


def test_trade_event_seq_is_idempotency_key(db):
    _stock(db)
    acct = _account(db)
    o = Order(account_id=acct.id, code="005930", side="BUY", order_type="MARKET", qty=10, status="PENDING")
    db.add(o)
    db.flush()

    def trade(seq):
        db.add(
            Trade(
                order_id=o.id,
                account_id=acct.id,
                code="005930",
                side="BUY",
                qty=5,
                price=70000,
                amount=350000,
                fee=52,
                event_seq=seq,
            )
        )
        db.flush()

    trade(1)
    trade(2)
    with pytest.raises(IntegrityError), db.begin_nested():  # 같은 (order_id, event_seq) 2회 → 1행
        trade(1)
    assert db.scalar(select(text("count(*)")).select_from(Trade)) == 2


def test_position_reserved_qty_check(db):
    _stock(db)
    acct = _account(db)
    pos = Position(account_id=acct.id, code="005930", qty=10, reserved_qty=10, avg_price=Decimal("71000.00"))
    db.add(pos)
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        pos.reserved_qty = 11
        db.flush()


def test_cash_ledger_type_affects_consistency(db):
    acct = _account(db)

    def row(type_, affects):
        db.add(CashLedger(account_id=acct.id, type=type_, affects=affects, amount=1, balance_after=1))
        db.flush()

    row("DEPOSIT", "cash")
    row("RESERVE", "reserved")
    row("RELEASE", "reserved")
    with pytest.raises(IntegrityError), db.begin_nested():
        row("RESERVE", "cash")  # RESERVE/RELEASE 는 reserved 에만 영향 (7.8)
    with pytest.raises(IntegrityError), db.begin_nested():
        row("BUY", "reserved")
    with pytest.raises(IntegrityError), db.begin_nested():
        row("BONUS", "cash")


def test_fee_config_has_no_seed_rows(db):
    """Q1 미결: 세율 시드는 오너 확인 전까지 비워 둔다."""
    assert db.scalar(select(text("count(*)")).select_from(FeeConfig)) == 0
    db.add(
        FeeConfig(
            effective_from=date(2026, 1, 1),
            buy_fee_rate=Decimal("0.0001"),
            sell_fee_rate=Decimal("0.0001"),
            sell_tax_rate=Decimal("0.002"),
        )
    )
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(FeeConfig(effective_from=date(2026, 1, 1), buy_fee_rate=0, sell_fee_rate=0, sell_tax_rate=0))
        db.flush()


def test_job_run_status_check(db):
    db.add(JobRun(job="collect_prices", status="success", items_ok=30))
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(JobRun(job="collect_prices", status="weird"))
        db.flush()
