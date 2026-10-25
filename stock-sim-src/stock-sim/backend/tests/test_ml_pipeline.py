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
    *(f"up_clf_{i}.txt" for i in range(5)),  # 시드 앙상블
    *(f"out_clf_{i}.txt" for i in range(5)),
    "q10.txt",
    "q50.txt",
    "q90.txt",
    "calibrator_up.pkl",
    "calibrator_out.pkl",
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
    assert set(FEATURES) <= set(json.loads((path / "feature_labels.json").read_text()))
    meta = json.loads((path / "meta.json").read_text())
    for key in (
        "train_period",
        "created_at",
        "data_hash",
        "versions",
        "best_iterations",
        "calibration",
        "up_target",
    ):
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


def test_metrics_report_skill_vs_base_rate(trained):
    """예측력 지표: 학습 구간 상승 비율(기후값)보다 Brier 가 나은지, IC 가 양수인 fold 수."""
    _, path = trained
    m = json.loads((path / "metrics.json").read_text())
    k = m["skill"]
    assert {"brier_model", "brier_base_rate", "brier_skill", "ic_p_up", "ic_positive_folds", "folds"} <= set(
        k
    )
    assert k["brier_skill"] == pytest.approx(1 - k["brier_model"] / k["brier_base_rate"], abs=1e-3)
    assert 0 <= k["ic_positive_folds"] <= k["folds"] == len(m["folds"])
    assert k["brier_skill"] > 0  # 심은 신호가 있으면 기후값보다 낫다


def test_rank_features_and_relative_target():
    """종목 피처는 같은 날 종목 간 백분위(_xr)로 바꾸고, 분류 라벨은 같은 날 중앙값 초과 여부(y_rel)."""
    prices, index, macro = make_synthetic(n_codes=5, n_days=300, seed=3)
    f = build_features(prices, index, macro)
    day = f[f["date"] == f["date"].max()]
    xr = [c for c in FEATURES if c.endswith("_xr")]
    assert xr and all(c[:-3] in f.columns for c in xr)
    r = day["ret_20d_xr"].to_numpy()
    assert np.allclose(sorted(r), [0.2, 0.4, 0.6, 0.8, 1.0])  # 5종목 백분위
    assert (day.sort_values("ret_20d")["ret_20d_xr"].diff().dropna() > 0).all()
    from src.features import build_targets

    t = build_targets(prices, index)
    t = t[t["ret_fwd"].notna()]
    share = t.groupby("date")["y_rel"].mean()
    assert share.between(0.39, 0.41).all()  # 5종목이면 중앙값 초과는 항상 2종목


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
        for f in row:  # 순위 피처 문장에는 원래 값과 백분위가 같이 나온다
            if f["feature"].endswith("_xr") and f["value"] is not None:
                assert f"백분위 {f['value'] * 100:.0f}" in f["label"]


def test_shap_values_sum_to_model_log_odds(trained, market):
    """SHAP 기여도 합 + 기댓값 = 보정 전 모델 출력(로그 오즈) — 설명이 실제 모델과 일치한다."""
    out, path = trained
    bundle = ModelBundle.load(out, path.name)
    X = build_features(*market).dropna(subset=["ret_60d"]).tail(5)
    ex = Explainer(bundle)
    sv = ex.shap_values(X)
    raw = bundle.up_clf.predict(X[FEATURES], raw_score=True)
    assert np.allclose(sv.sum(axis=1) + ex.expected_value, raw, atol=1e-6)


# ---------------------------------------------------------------- 피처 세부 규칙
def test_label_sentences_cover_all_features():
    assert set(FEATURES) <= set(FEATURE_LABELS)
    assert label_for("ret_20d", 0.083) == "최근 20거래일 +8.3% 상승"  # SPEC FR-PR-6 예시
    assert label_for("ma20_gap", -0.021) == "20일 이동평균 대비 -2.1% 아래"
    assert label_for("foreign_net_5d", 0.0012) == "최근 5일 외국인 순매수 (시총 대비 +0.12%)"
    assert label_for("rsi_14", 25) == "RSI(14) 25, 침체 구간"
    assert "데이터 없음" in label_for("usdkrw_chg_5d", float("nan"))
    # 순위 피처: 원래 값 문장 + 같은 날 백분위
    assert label_for("ret_20d_xr", 0.9, 0.083) == "최근 20거래일 +8.3% 상승 (같은 날 종목 중 백분위 90)"
    assert label_for("ret_20d_xr", 0.9) == "최근 20거래일 같은 날 종목 중 백분위 90"
    assert "데이터 없음" in label_for("ret_20d_xr", float("nan"))


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


# ---------------------------------------------------------------- 시점별 유니버스 (생존 편향 제거)
def test_point_in_time_universe_ranks_within_members_only():
    from src.features import build_targets, in_universe

    prices, index, macro = make_synthetic(n_codes=6, n_days=120, seed=5)
    codes = sorted(prices["code"].unique())
    days = sorted(pd.to_datetime(prices["date"]).unique())
    month1 = pd.Timestamp(days[0]).to_period("M")
    first_of = lambda per: min(d for d in days if pd.Timestamp(d).to_period("M") == per)  # noqa: E731
    month2 = next(pd.Timestamp(d).to_period("M") for d in days if pd.Timestamp(d).to_period("M") != month1)
    members = pd.DataFrame(
        {
            "date": [first_of(month1)] * 3 + [first_of(month2)] * 4,
            "code": codes[:3] + codes[2:6],  # 둘째 달에 명단이 바뀐다
        }
    )
    f = build_features(prices, index, macro, members)
    for per, want in ((month1, set(codes[:3])), (month2, set(codes[2:6]))):
        got = f[f["date"].dt.to_period("M") == per]
        assert set(got["code"]) == want  # 그 달 명단 종목만
        day = got[got["date"] == got["date"].max()]
        n = len(day)
        assert sorted(day["ret_20d_xr"].dropna()) == pytest.approx(
            [(i + 1) / n for i in range(n)]
        )  # 명단 안 순위
    t = build_targets(prices, index, members=members)
    full = build_features(prices, index, macro)
    assert len(t) == len(in_universe(full, members)) == len(f)


def test_training_with_members_evaluates_on_service_universe(tmp_path):
    prices, index, macro = make_synthetic(n_codes=12, n_days=700, seed=7, signal_strength=0.01)
    codes = sorted(prices["code"].unique())
    days = pd.to_datetime(prices["date"])
    firsts = days.groupby(days.dt.to_period("M")).min()
    members = pd.DataFrame([(d, c) for d in firsts for c in codes[:10]], columns=["date", "code"])
    service = prices[prices["code"].isin(codes[:6])]
    path = T.run(prices, index, macro, tmp_path, now=NOW, members=members, eval_prices=service, **FOLD_KW)
    meta = json.loads((path / "meta.json").read_text())
    assert meta["n_codes"] == 10 and meta["eval_universe"] == "서비스 종목"
    m = json.loads((path / "metrics.json").read_text())
    test_codes_n = m["strategies"]["model"]["n"]
    service_rows = len(T.make_dataset(service, index, macro).dropna(subset=["y_up"]))
    assert 0 < test_codes_n < service_rows  # 테스트 행은 서비스 종목에서만 (전체 기간의 일부)


def test_valuation_features_inverse_and_missing_when_nonpositive():
    prices, index, macro = make_synthetic(n_codes=3, n_days=80, seed=2)
    last = prices["date"].max()
    p = prices.assign(per=10.0, pbr=2.0, div_yield=3.0)
    p.loc[(p["code"] == p["code"].min()) & (p["date"] == last), ["per", "pbr"]] = [0.0, -1.0]  # 적자/자본잠식
    f = build_features(p, index, macro)
    day = f[f["date"] == pd.Timestamp(last)].set_index("code")
    first = day.index.min()
    assert pd.isna(day.loc[first, "earn_yield"]) and pd.isna(day.loc[first, "book_to_price"])
    other = day.drop(first)
    assert (other["earn_yield"] == 0.1).all() and (other["book_to_price"] == 0.5).all()
    assert (day["div_yield"] == 0.03).all()
    assert {"earn_yield_xr", "book_to_price_xr", "div_yield_xr"} <= set(FEATURES)
    # 밸류에이션 열이 없는 데이터(합성, 펀드)는 결측으로 계산된다
    assert build_features(prices, index, macro)["earn_yield"].isna().all()


def test_seed_ensemble_averages_log_odds_and_loads_old_single_file(trained, tmp_path, market):
    from src.ensemble import SeedEnsemble

    _, path = trained
    ens = SeedEnsemble.load(path, "up_clf")
    assert len(ens.boosters) == 5
    assert json.loads((path / "meta.json").read_text())["ensemble_size"] == 5
    X = build_features(*market).dropna(subset=["ret_20d"]).head(50)[FEATURES]
    raws = [b.predict(X, raw_score=True) for b in ens.boosters]
    assert not np.allclose(raws[0], raws[1])  # 시드마다 다른 모델
    assert np.allclose(ens.predict(X, raw_score=True), np.mean(raws, axis=0))
    assert np.allclose(ens.predict(X), 1 / (1 + np.exp(-np.mean(raws, axis=0))))
    ens.boosters[0].save_model(str(tmp_path / "up_clf.txt"))  # 예전 산출물 형식 (단일 파일)
    assert len(SeedEnsemble.load(tmp_path, "up_clf").boosters) == 1


def test_rank_returns_table_from_walk_forward_test_rows(trained):
    _, path = trained
    m = json.loads((path / "metrics.json").read_text())
    rr = m["rank_returns"]
    assert [r["bin"] for r in rr] == list(range(len(rr))) and 2 <= len(rr) <= 10
    assert sum(r["n"] for r in rr) == m["strategies"]["model"]["n"]  # 테스트 행 전부가 한 구간에
    assert all(0 <= r["up_ratio"] <= 1 for r in rr)
    # 심은 신호가 있으면 상위 구간의 실제 수익률이 하위 구간보다 높다
    assert rr[0]["mean_return"] > rr[-1]["mean_return"]


def test_rank_returns_bins_by_daily_percentile():
    d = pd.DataFrame(
        {
            "date": ["d1"] * 10 + ["d2"] * 10,
            "p_up": list(range(10, 0, -1)) * 2,  # 첫 행이 1위
            "ret_fwd": [0.10 - 0.01 * i for i in range(10)] * 2,
        }
    )
    rr = T.rank_returns(d)
    assert [r["n"] for r in rr] == [2] * 10
    assert rr[0]["mean_return"] == pytest.approx(0.10) and rr[9]["mean_return"] == pytest.approx(0.01)
    assert rr[0]["mean_excess"] > 0 > rr[9]["mean_excess"]
