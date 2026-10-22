"""일일 예측 배치와 예측/성적표 API (SPEC 6.3, 6.5, 10.2, Phase 6 완료 기준). 가짜 LLM, 실제 DB, 합성 데이터."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time
from decimal import Decimal

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select
from src import train_lgbm as T
from src.synthetic import make_synthetic

from app.core.config import settings
from app.core.db import get_db
from app.ml.explain import Explainer
from app.ml.infer import ModelBundle
from app.models import (
    IndexDaily,
    ModelMetric,
    News,
    NewsStockMap,
    Prediction,
    PredictionFactor,
    PredictionNews,
    PredictionOutcome,
    PredictionScenario,
    PriceDaily,
    Stock,
)
from app.services import prediction_service as ps
from app.services.market_data import KST
from app.services.scenario_service import LLMResult
from app.services.ticks import is_valid_tick

N_CODES = 6
FOLD_KW = {"train_days": 200, "valid_days": 40, "test_days": 40, "step_days": 40, "embargo": 5}


class GoodLLM:
    """입력값만 써서 규칙을 지키는 가짜 LLM."""

    model = "fake-llm"

    def __init__(self):
        self.calls = 0

    def generate(self, system, user):
        self.calls += 1
        inp = json.loads(user)
        p = inp["예측"]
        out = {
            "summary": f"{inp['종목명']}의 5거래일 상승확률은 {p['5거래일_상승확률']}입니다. 관련 요인을 함께 봅니다.",
            "bull": f"상단은 {p['예상_가격_범위']['상단']} 부근입니다. 과거 유사 사례에서는 수급이 함께 나타났습니다.",
            "base": f"중앙 예측은 {p['예상_수익률_범위']['중앙']}입니다. 범위 안의 등락을 함께 봅니다.",
            "bear": f"하단은 {p['예상_가격_범위']['하단']} 부근입니다. 예측에는 불확실성이 있습니다.",
        }
        return LLMResult(json.dumps(out, ensure_ascii=False), self.model, 120, 80)


class LyingLLM(GoodLLM):
    """입력에 없는 숫자를 만들어 낸다 -> 재시도 후 템플릿."""

    def generate(self, system, user):
        r = super().generate(system, user)
        out = json.loads(r.text)
        out["bull"] = "상단은 37.5% 상승입니다. 확실한 흐름입니다."
        return LLMResult(json.dumps(out, ensure_ascii=False), self.model, 120, 80)


class DownLLM(GoodLLM):
    def generate(self, system, user):
        self.calls += 1
        raise ConnectionError("LLM 장애")


@pytest.fixture(scope="module")
def market():
    return make_synthetic(n_codes=N_CODES, n_days=420, seed=5, signal_strength=0.01)


@pytest.fixture(scope="module")
def model_dir(market, tmp_path_factory):
    d = tmp_path_factory.mktemp("models")
    path = T.run(*market, d, now=datetime(2026, 10, 22, tzinfo=UTC), **FOLD_KW)
    return d, path.name


@pytest.fixture
def seeded(sf, market):
    prices, index, _ = market
    with sf() as s:
        for code in sorted(prices["code"].unique()):
            s.add(Stock(code=code, name=f"종목{code[-2:]}"))
        s.flush()
        rows = prices.assign(date=pd.to_datetime(prices["date"]).dt.date)
        cols = [
            "code",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "value",
            "foreign_net",
            "inst_net",
            "market_cap",
        ]
        recs = []
        for r in rows[cols].itertuples(index=False):
            d = r._asdict()
            for k in (
                "open",
                "high",
                "low",
                "close",
                "volume",
                "value",
                "foreign_net",
                "inst_net",
                "market_cap",
            ):
                d[k] = None if pd.isna(d[k]) else int(d[k])
            recs.append(d)
        s.execute(insert(PriceDaily), recs)
        s.execute(
            insert(IndexDaily),
            [
                {"index_code": "KOSPI", "date": pd.Timestamp(d).date(), "close": Decimal(f"{c:.4f}")}
                for d, c in zip(index["date"], index["close"], strict=True)
            ],
        )
        s.commit()
    days = sorted(pd.to_datetime(prices["date"]).dt.date.unique())
    return days


@pytest.fixture
def ctx(model_dir):
    d, version = model_dir
    bundle = ModelBundle.load(d, version)
    return ps.BatchContext(bundle, Explainer(bundle), GoodLLM())


def _count(sf, model):
    with sf() as s:
        return s.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------- 배치 전체 (mock LLM)
def test_batch_creates_predictions_factors_scenarios_FR_PR_1_2_5(sf, seeded, ctx, model_dir):
    as_of = seeded[-20]
    res = ps.run_daily_batch(sf, ctx, model_dir[0], as_of)
    assert res.status() == "success" and res.ok == N_CODES
    with sf() as s:
        preds = s.scalars(select(Prediction)).all()
        assert len(preds) == N_CODES
        for p in preds:
            assert p.as_of_date == as_of and p.horizon == 5 and p.model_version == ctx.bundle.version
            assert 0 <= p.p_up <= 1 and p.q10 <= p.q50 <= p.q90  # FR-PR-3
            factors = s.scalars(select(PredictionFactor).where(PredictionFactor.prediction_id == p.id)).all()
            assert [f.rank for f in sorted(factors, key=lambda f: f.rank)] == [1, 2, 3, 4, 5]  # FR-PR-5
            assert all(f.label for f in factors)  # FR-PR-6
            sc = s.get(PredictionScenario, p.id)
            assert (
                sc.generated_by == "llm" and sc.llm_model == "fake-llm" and sc.input_tokens == 120
            )  # FR-SC-6
    assert ctx.llm.calls == N_CODES


def test_rerun_skips_existing_predictions_FR_PR_9(sf, seeded, ctx, model_dir):
    as_of = seeded[-20]
    ps.run_daily_batch(sf, ctx, model_dir[0], as_of)
    with sf() as s:
        first = {p.code: (p.id, p.p_up) for p in s.scalars(select(Prediction))}
    res = ps.run_daily_batch(sf, ctx, model_dir[0], as_of)
    with sf() as s:
        again = {p.code: (p.id, p.p_up) for p in s.scalars(select(Prediction))}
    assert first == again and res.ok == 0  # 덮어쓰지 않고 건너뜀
    assert _count(sf, PredictionScenario) == N_CODES


def test_invalid_llm_output_falls_back_to_template_but_saves_prediction(sf, seeded, ctx, model_dir):
    ctx.llm = LyingLLM()
    ps.run_daily_batch(sf, ctx, model_dir[0], seeded[-20])
    with sf() as s:
        scs = s.scalars(select(PredictionScenario)).all()
    assert len(scs) == N_CODES and {x.generated_by for x in scs} == {"template"}
    assert ctx.llm.calls == 2 * N_CODES  # 종목마다 1회 재시도 (FR-SC-4)
    assert all("37.5%" not in x.bull for x in scs)


def test_llm_outage_still_saves_predictions_FR_SC_5(sf, seeded, ctx, model_dir):
    ctx.llm = DownLLM()
    res = ps.run_daily_batch(sf, ctx, model_dir[0], seeded[-20])
    assert res.status() == "success" and _count(sf, Prediction) == N_CODES
    with sf() as s:
        assert {x.generated_by for x in s.scalars(select(PredictionScenario))} == {"template"}


def test_one_stock_failure_does_not_stop_batch(sf, seeded, ctx, model_dir, monkeypatch):
    real = ctx.explainer.top_factors
    bad_code = sorted({c for c in make_synthetic(n_codes=N_CODES, n_days=10)[0]["code"]})[0]

    def flaky(X, k=5):
        if X["code"].iloc[0] == bad_code:
            raise ValueError("SHAP 실패")
        return real(X, k)

    monkeypatch.setattr(ctx.explainer, "top_factors", flaky)
    res = ps.run_daily_batch(sf, ctx, model_dir[0], seeded[-20])
    assert res.status() == "partial" and res.ok == N_CODES - 1
    assert any(bad_code in f for f in res.failed)


def test_catch_up_predicts_missing_days(sf, seeded, ctx, model_dir):
    ps.run_daily_batch(sf, ctx, model_dir[0], seeded[-4])
    res = ps.run_daily_batch(sf, ctx, model_dir[0])  # as_of 없이: 마지막 예측일 이후 가격 날짜를 채운다
    with sf() as s:
        dates = sorted(set(s.scalars(select(Prediction.as_of_date))))
    assert dates == seeded[-4:] and res.ok == 3 * N_CODES


def test_scenario_input_uses_display_strings_and_tick_rounded_prices(sf, seeded, ctx, model_dir):
    captured = []

    class Spy(GoodLLM):
        def generate(self, system, user):
            captured.append(json.loads(user))
            return super().generate(system, user)

    ctx.llm = Spy()
    ps.run_daily_batch(sf, ctx, model_dir[0], seeded[-20])
    inp = captured[0]
    assert set(inp) == {
        "종목명",
        "기준일",
        "기준일_종가",
        "예측",
        "근거_TOP5",
        "관련_뉴스",
        "시장_상황",
    }  # FR-SC-1
    assert len(inp["근거_TOP5"]) == 5 and inp["예측"]["5거래일_상승확률"].endswith("%")
    for v in inp["예측"]["예상_가격_범위"].values():
        assert is_valid_tick(int(v.replace(",", "").replace("원", "")))  # FR-PR-4
    assert "KOSPI_5일_수익률" in inp["시장_상황"]


# ---------------------------------------------------------------- 관련 뉴스 (FR-PR-7)
def test_news_attached_from_last_3_trading_days_with_relevance(sf, seeded, ctx, model_dir):
    as_of = seeded[-20]
    code = sorted(make_synthetic(n_codes=N_CODES, n_days=10)[0]["code"].unique())[0]
    at = lambda d, h: datetime.combine(d, time(h, 0), tzinfo=KST)  # noqa: E731
    items = [
        ("in-1", at(as_of, 10), Decimal("1.0")),  # 기준일 장중 -> 포함
        ("in-2", at(seeded[-21], 9), Decimal("0.6")),  # 직전 거래일 -> 포함
        ("in-3", at(seeded[-22], 16), Decimal("0.6")),  # 2거래일 전 마감 후 -> 다음 거래일(창 안) -> 포함
        ("low", at(as_of, 11), Decimal("0.3")),  # 관련도 낮음 -> 제외
        ("old", at(seeded[-25], 10), Decimal("1.0")),  # 창 밖 -> 제외
        ("after", at(as_of, 16), Decimal("1.0")),  # 기준일 마감 후 -> 다음 거래일 피처 -> 제외 (누수 방지)
    ]
    with sf() as s:
        for title, ts, rel in items:
            n = News(url=f"https://n.example/{title}", title=title, published_at=ts)
            s.add(n)
            s.flush()
            s.add(NewsStockMap(news_id=n.id, code=code, relevance=rel))
        s.commit()
    ps.run_daily_batch(sf, ctx, model_dir[0], as_of)
    with sf() as s:
        pid = s.scalar(select(Prediction.id).where(Prediction.code == code))
        titles = set(
            s.scalars(
                select(News.title)
                .join(PredictionNews, PredictionNews.news_id == News.id)
                .where(PredictionNews.prediction_id == pid)
            )
        )
    assert titles == {"in-1", "in-2", "in-3"}


# ---------------------------------------------------------------- 결과 채우기 / 지표 (FR-PF)
def test_fill_outcomes_after_five_trading_days_FR_PF_1(sf, seeded, ctx, model_dir):
    as_of, fresh = seeded[-20], seeded[-3]
    ps.run_daily_batch(sf, ctx, model_dir[0], as_of)
    ps.run_daily_batch(sf, ctx, model_dir[0], fresh)  # 아직 5거래일이 안 지남
    with sf() as s:
        outs = s.execute(select(Prediction, PredictionOutcome).join(PredictionOutcome)).all()
        assert len(outs) == N_CODES and all(p.as_of_date == as_of for p, _ in outs)
        p, o = outs[0]
        assert o.target_date == seeded[-15]  # 기준일 + 5거래일
        c0 = s.get(PriceDaily, (p.code, as_of)).close
        c1 = s.get(PriceDaily, (p.code, seeded[-15])).close
        assert float(o.actual_return) == pytest.approx(c1 / c0 - 1, abs=1e-6)
        assert o.hit_direction == ((float(p.p_up) >= 0.5) == (c1 / c0 - 1 > 0))
        assert o.in_range == (float(p.q10) <= c1 / c0 - 1 <= float(p.q90))
        assert o.actual_excess is not None
        assert ps.fill_outcomes(s) == 0  # 멱등


def test_live_and_offline_metrics_stored_FR_PF_2_5(sf, seeded, ctx, model_dir):
    for d in seeded[-30:-15]:
        ps.run_daily_batch(sf, ctx, model_dir[0], d)
    with sf() as s:
        rows = s.scalars(select(ModelMetric)).all()
    live = {(r.window, r.strategy, r.metric) for r in rows if r.scope == "live"}
    for w in ("20", "60", "250", "all"):
        for strat in ("model", "always_up", "momentum", "kospi"):
            assert (w, strat, "brier") in live and (w, strat, "direction_accuracy") in live
    offline = [r for r in rows if r.scope == "offline"]
    assert {r.strategy for r in offline} == {"model", "always_up", "momentum", "kospi"}
    n_live = {r.n for r in rows if r.scope == "live" and r.window == "all" and r.strategy == "model"}
    assert n_live == {15 * N_CODES}


def test_realized_quantiles_use_only_past_outcomes():
    days = pd.bdate_range("2026-01-01", periods=40)
    px = pd.DataFrame({"code": "A", "date": [d.date() for d in days], "fwd_5d": [0.01] * 30 + [9.9] * 10})
    lo, mid, hi = ps.realized_quantiles(px, days[35].date())
    assert hi < 1  # first 직전 5일의 값(아직 결과 미확정)과 그 이후 값은 쓰지 않는다


# ---------------------------------------------------------------- API (SPEC 10.2)
@pytest.fixture
def client(sf, monkeypatch, model_dir):
    monkeypatch.setattr(settings, "model_version", model_dir[1])
    monkeypatch.setattr(settings, "model_dir", str(model_dir[0]))

    def db():
        with sf() as s:
            yield s

    from app.main import app

    app.dependency_overrides[get_db] = db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_prediction_api_shape_and_disclaimer(sf, seeded, ctx, model_dir, client):
    ps.run_daily_batch(sf, ctx, model_dir[0], seeded[-20])
    code = sorted(make_synthetic(n_codes=N_CODES, n_days=10)[0]["code"].unique())[0]
    r = client.get(f"/api/v1/stocks/{code}/prediction")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["disclaimer"] == "본 서비스는 교육·모의투자 목적이며 예측은 투자 권유가 아닙니다."
    d = body["data"]
    assert (
        d["horizon"] == 5
        and d["model_version"] == model_dir[1]
        and d["as_of_date"] == seeded[-20].isoformat()
    )
    rg = d["range"]
    assert rg["q10"] <= rg["q50"] <= rg["q90"] and rg["price_low"] <= rg["price_mid"] <= rg["price_high"]
    assert all(is_valid_tick(rg[k]) for k in ("price_low", "price_mid", "price_high"))
    assert [f["rank"] for f in d["factors"]] == [1, 2, 3, 4, 5] and all(
        f["direction"] in "+-" for f in d["factors"]
    )
    assert d["scenarios"]["generated_by"] == "llm" and set(d["scenarios"]) >= {
        "summary",
        "bull",
        "base",
        "bear",
    }


def test_prediction_api_404s(sf, seeded, client):
    code = sorted(make_synthetic(n_codes=N_CODES, n_days=10)[0]["code"].unique())[0]
    r = client.get(f"/api/v1/stocks/{code}/prediction")
    assert r.status_code == 404 and r.json()["error"]["code"] == "PREDICTION_NOT_FOUND"
    assert client.get("/api/v1/stocks/999999/prediction").json()["error"]["code"] == "UNKNOWN_SYMBOL"


def test_history_api_includes_outcomes_and_summary(sf, seeded, ctx, model_dir, client):
    for d in (seeded[-20], seeded[-19], seeded[-2]):
        ps.run_daily_batch(sf, ctx, model_dir[0], d)
    code = sorted(make_synthetic(n_codes=N_CODES, n_days=10)[0]["code"].unique())[0]
    body = client.get(f"/api/v1/stocks/{code}/prediction/history?limit=10").json()
    items = body["data"]
    assert [i["as_of_date"] for i in items] == [d.isoformat() for d in (seeded[-2], seeded[-19], seeded[-20])]
    assert items[0]["outcome"] is None and items[1]["outcome"] is not None
    assert body["meta"]["summary"]["n"] == 2 and "disclaimer" in body["meta"]


def test_performance_api_live_and_offline(sf, seeded, ctx, model_dir, client):
    for d in seeded[-30:-15]:
        ps.run_daily_batch(sf, ctx, model_dir[0], d)
    live = client.get("/api/v1/model/performance?window=20&scope=live").json()
    d = live["data"]
    assert (
        d["scope"] == "live"
        and d["window"] == "20"
        and set(d["strategies"]) == {"model", "always_up", "momentum", "kospi"}
    )
    assert len(d["calibration_up"]) == 10 and d["range_coverage_target"] == 0.8
    assert d["n"] == 15 * N_CODES and set(d["best"]) >= {"brier", "direction_accuracy"}
    assert "disclaimer" in live["meta"]
    off = client.get("/api/v1/model/performance?scope=offline&window=60").json()["data"]
    assert off["scope"] == "offline" and off["window"] == "all" and off["calibration_up"]


def test_performance_api_without_live_results_explains_why(sf, seeded, client):
    body = client.get("/api/v1/model/performance").json()
    assert body["data"]["n"] == 0 and "5거래일" in body["meta"]["note"]


def test_stock_list_shows_latest_p_up(sf, seeded, ctx, model_dir, client):
    ps.run_daily_batch(sf, ctx, model_dir[0], seeded[-3])
    rows = client.get("/api/v1/stocks").json()["data"]
    assert all(r["p_up"] is not None and r["as_of_date"] == seeded[-3].isoformat() for r in rows)


def test_scheduler_post_close_runs_batch_after_prices(sf, monkeypatch):
    from app.pipelines import collect_prices, daily_batch, scheduler

    calls = []
    monkeypatch.setattr(collect_prices, "run", lambda **kw: calls.append("prices"))
    monkeypatch.setattr(daily_batch, "run", lambda *a, **kw: calls.append("batch"))
    scheduler.post_close(sf, today=datetime(2026, 10, 21).date())
    assert calls == ["prices", "batch"]  # SPEC 12: 가격 수집 -> 예측 배치


def test_batch_context_requires_model_version(monkeypatch):
    from app.pipelines import daily_batch

    monkeypatch.setattr(settings, "model_version", "")
    assert daily_batch.build_context(settings) is None
