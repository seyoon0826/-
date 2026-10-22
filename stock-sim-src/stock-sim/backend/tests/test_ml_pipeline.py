"""합성 데이터로 학습 -> 산출물 -> 서버 로드 -> 추론 -> SHAP 근거까지 end-to-end (Phase 5 완료 기준)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from src import evaluate as ev
from src import train_lgbm as T
from src.synthetic import make_synthetic

from app.ml.explain import Explainer
from app.ml.features import FEATURE_LABELS, FEATURES, build_features, label_for
from app.ml.infer import FeatureMismatch, ModelBundle, ModelNotFound, serving_frame

FOLD_KW = {"train_days": 300, "valid_days": 60, "test_days": 60, "step_days": 60, "embargo": 5}
NOW = datetime(2026, 10, 22, tzinfo=UTC)
ARTIFACTS = {
    "up_clf.txt",
    "out_clf.txt",
    "q10.txt",
    "q50.txt",
    "q90.txt",
    "calibrator_up.pkl",
    "features.json",
    "feature_labels.json",
    "metrics.json",
    "meta.json",
    "params.json",
}


@pytest.fixture(scope="module")
def market():
    return make_synthetic(n_codes=10, n_days=700, seed=11, signal_strength=0.01)  # 학습 가능한 신호를 키운다


@pytest.fixture(scope="module")
def trained(market, tmp_path_factory):
    out = tmp_path_factory.mktemp("models")
    path = T.run(*market, out, now=NOW, **FOLD_KW)
    return out, path


# ---------------------------------------------------------------- 산출물 (SPEC 8.4)
def test_artifacts_follow_spec_layout(trained):
    out, path = trained
    assert (
        path.parent == out
        and path.name.startswith("lgbm-20261022-")
        and len(path.name) == len("lgbm-20261022-") + 6
    )
    assert {p.name for p in path.iterdir()} == ARTIFACTS
    assert json.loads((path / "features.json").read_text()) == FEATURES
    assert set(json.loads((path / "feature_labels.json").read_text())) == set(FEATURES)
    meta = json.loads((path / "meta.json").read_text())
    for key in ("train_period", "created_at", "data_hash", "versions", "best_iterations"):
        assert key in meta
    assert meta["versions"]["lightgbm"] and len(meta["data_hash"]) == 64


def test_metrics_compare_model_with_three_baselines_offline(trained):
    _, path = trained
    m = json.loads((path / "metrics.json").read_text())
    assert m["scope"] == "offline" and len(m["folds"]) >= 3  # FR-PF-5: 오프라인 결과임을 표기
    assert set(m["strategies"]) == {"model", "always_up", "momentum", "kospi"}
    for s in m["strategies"].values():
        assert {
            "direction_accuracy",
            "brier",
            "pinball_q10",
            "pinball_q50",
            "pinball_q90",
            "range_coverage",
            "ic",
            "n",
        } <= set(s)
    assert len(m["calibration_up"]) == 10
    assert sum(b["n"] for b in m["calibration_up"]) == m["strategies"]["model"]["n"]


def test_model_learns_planted_signal_better_than_baselines(trained):
    """합성 데이터에 심은 신호(전일 수급 -> 수익률)를 모델이 배운다: IC/Brier 가 베이스라인보다 낫다."""
    _, path = trained
    s = json.loads((path / "metrics.json").read_text())["strategies"]
    assert s["model"]["ic"] > 0.05
    assert s["model"]["brier"] < min(s[k]["brier"] for k in ("always_up", "momentum", "kospi"))
    assert s["model"]["pinball_q50"] <= s["momentum"]["pinball_q50"]


def test_training_is_reproducible(market, tmp_path):
    """같은 데이터·시드 -> 같은 지표와 같은 모델 버전 (SPEC 14)."""
    a = T.run(*market, tmp_path / "a", now=NOW, **FOLD_KW)
    b = T.run(*market, tmp_path / "b", now=NOW, **FOLD_KW)
    assert a.name == b.name
    assert (a / "metrics.json").read_text() == (b / "metrics.json").read_text()


# ---------------------------------------------------------------- 서버 로드 / 추론
def test_server_loads_bundle_and_predicts(trained, market):
    out, path = trained
    bundle = ModelBundle.load(out, path.name)
    prices, index, macro = market
    as_of = pd.Timestamp(sorted(prices["date"].unique())[-1]).date()
    X = serving_frame(prices, index, macro, as_of)
    pred = bundle.predict(X)
    assert len(pred) == 10 and not pred.isna().any().any()
    assert ((pred["p_up"] >= 0) & (pred["p_up"] <= 1)).all()
    assert ((pred["q10"] <= pred["q50"]) & (pred["q50"] <= pred["q90"])).all()  # FR-PR-3


def test_quantiles_are_sorted_even_if_raw_models_cross(trained, market, monkeypatch):
    out, path = trained
    bundle = ModelBundle.load(out, path.name)
    X = build_features(*market).dropna(subset=["ret_20d"]).head(20)
    monkeypatch.setattr(bundle.quantiles["q10"], "predict", lambda x: np.full(len(x), 0.05))
    monkeypatch.setattr(bundle.quantiles["q90"], "predict", lambda x: np.full(len(x), -0.05))
    p = bundle.predict(X)
    assert (p["q10"] <= p["q50"]).all() and (p["q50"] <= p["q90"]).all()


def test_startup_refuses_mismatched_features(trained, tmp_path):
    out, path = trained
    bad = tmp_path / "bad"
    bad.mkdir()
    for f in path.iterdir():
        (bad / f.name).write_bytes(f.read_bytes())
    (bad / "features.json").write_text(json.dumps(list(reversed(FEATURES))))  # 순서만 달라도 거부
    with pytest.raises(FeatureMismatch):
        ModelBundle.load(tmp_path, "bad")
    with pytest.raises(ModelNotFound):
        ModelBundle.load(tmp_path, "nope")


def test_api_lifespan_loads_model_or_fails_to_start(trained, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app

    out, path = trained
    monkeypatch.setattr(settings, "model_dir", str(out))
    monkeypatch.setattr(settings, "model_version", path.name)
    with TestClient(app):
        assert app.state.model.version == path.name
    monkeypatch.setattr(settings, "model_version", "lgbm-00000000-000000")
    with pytest.raises(ModelNotFound), TestClient(app):
        pass  # 설정한 모델이 없으면 기동 실패


# ---------------------------------------------------------------- SHAP 근거 (FR-PR-5/6)
def test_top5_factors_have_spec_shape_and_sentences(trained, market):
    out, path = trained
    bundle = ModelBundle.load(out, path.name)
    prices, index, macro = market
    X = serving_frame(prices, index, macro, pd.Timestamp(sorted(prices["date"].unique())[-1]).date())
    factors = Explainer(bundle).top_factors(X.head(3))
    assert len(factors) == 3
    for row in factors:
        assert [f["rank"] for f in row] == [1, 2, 3, 4, 5]
        assert all(set(f) == {"rank", "feature", "value", "shap_value", "direction", "label"} for f in row)
        mags = [abs(f["shap_value"]) for f in row]
        assert mags == sorted(mags, reverse=True)  # 절댓값 내림차순
        assert all(f["direction"] == ("+" if f["shap_value"] >= 0 else "-") for f in row)
        assert all(f["feature"] in FEATURES and f["label"] for f in row)


def test_shap_values_sum_to_model_log_odds(trained, market):
    """SHAP 기여도 합 + 기댓값 = 보정 전 모델 출력(로그 오즈) — 설명이 실제 모델과 일치한다."""
    out, path = trained
    bundle = ModelBundle.load(out, path.name)
    X = build_features(*market).dropna(subset=["ret_60d"]).tail(5)
    ex = Explainer(bundle)
    sv = ex.shap_values(X)
    raw = bundle.up_clf.predict(X[FEATURES], raw_score=True)
    assert np.allclose(sv.sum(axis=1) + ex._tree.expected_value, raw, atol=1e-6)


# ---------------------------------------------------------------- 피처 세부 규칙
def test_label_sentences_cover_all_features():
    assert set(FEATURE_LABELS) == set(FEATURES)
    assert label_for("ret_20d", 0.083) == "최근 20거래일 +8.3% 상승"  # SPEC FR-PR-6 예시
    assert label_for("ma20_gap", -0.021) == "20일 이동평균 대비 -2.1% 아래"
    assert label_for("foreign_net_5d", 0.0012) == "최근 5일 외국인 순매수 (시총 대비 +0.12%)"
    assert label_for("rsi_14", 25) == "RSI(14) 25, 침체 구간"
    assert "데이터 없음" in label_for("usdkrw_chg_5d", float("nan"))


def _one_stock(n=30, **override):
    d = pd.bdate_range("2026-01-02", periods=n)
    base = {
        "code": "000001",
        "date": d,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 1_000.0,
        "value": 100_000.0,
        "foreign_net": 10.0,
        "inst_net": 0.0,
        "market_cap": 1_000_000.0,
    }
    base.update(override)
    return pd.DataFrame(base)


def test_flow_missing_days_are_rescaled_and_coverage_reported():
    p = _one_stock()
    p.loc[p.index[-2], "foreign_net"] = np.nan  # 최근 5일 중 하루 결측
    f = build_features(p).iloc[-1]
    assert f["foreign_net_5d"] == pytest.approx(50 / 1_000_000)  # 4일 합 40 x 5/4 = 50
    assert f["flow_coverage_5d"] == pytest.approx(0.8)
    p.loc[p.index[-5:], "foreign_net"] = np.nan  # 창 전체 결측 -> NaN
    f = build_features(p).iloc[-1]
    assert np.isnan(f["foreign_net_5d"]) and f["flow_coverage_5d"] == 0


def test_halted_days_are_counted_separately_from_zero_flow():
    p = _one_stock()
    p.loc[p.index[-3:], ["volume", "foreign_net"]] = 0  # 거래정지 3일 (순매수는 저장 규칙대로 0)
    f = build_features(p).iloc[-1]
    assert f["halted_20d"] == 3 and f["flow_coverage_5d"] == 1.0


def test_rsi_bounds_and_value_rank_is_cross_sectional():
    a = _one_stock(close=np.linspace(100, 130, 30), high=np.linspace(101, 131, 30))
    b = _one_stock(code="000002", value=1.0)
    f = build_features(pd.concat([a, b]))
    last = f[f["date"] == f["date"].max()].set_index("code")
    assert last.loc["000001", "rsi_14"] == 100.0  # 계속 오르기만 함
    assert last.loc["000001", "value_rank"] == 1.0 and last.loc["000002", "value_rank"] == 0.5


def test_macro_uses_last_published_value_only():
    p = _one_stock(10)
    days = p["date"]
    macro = pd.DataFrame(
        {"series": ["base_rate", "base_rate"], "date": [days[2], days[7]], "value": [3.5, 3.25]}
    )
    f = build_features(p, macro=macro).set_index("date")
    assert np.isnan(f.loc[days[1], "base_rate"])
    assert f.loc[days[6], "base_rate"] == 3.5 and f.loc[days[7], "base_rate"] == 3.25  # 발표 전에는 이전 값


def test_evaluate_metrics_known_values():
    assert ev.brier(np.array([1.0, 0.0]), np.array([1, 1])) == 0.5
    assert ev.pinball(np.array([0.0]), np.array([1.0]), 0.9) == pytest.approx(0.9)
    assert ev.pinball(np.array([0.0]), np.array([-1.0]), 0.9) == pytest.approx(0.1)
    assert ev.coverage(np.array([-1, -1]), np.array([1, 1]), np.array([0, 2])) == 0.5
    d = pd.Series(["a"] * 5 + ["b"] * 5)
    assert ev.information_coefficient(d, np.arange(10.0), np.arange(10.0)) == pytest.approx(1.0)
