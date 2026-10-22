"""학습/검증 분할에서 미래 정보 누수가 없는지 (뉴스 시점 정렬 포함) 검사 (SPEC 8.1, Phase 5 완료 기준)."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from src.features import build_targets
from src.synthetic import make_synthetic
from src.walk_forward import make_folds

from app.ml.features import FEATURES, HORIZON, build_features, news_feature_date
from app.ml.infer import serving_frame
from app.services.market_data import KST

FOLD_KW = {"train_days": 300, "valid_days": 40, "test_days": 40, "step_days": 40, "embargo": 5}


@pytest.fixture(scope="module")
def market():
    return make_synthetic(n_codes=8, n_days=600, seed=3)


def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    return bool(np.allclose(a.to_numpy(float), b.to_numpy(float), equal_nan=True, rtol=0, atol=0))


# ---------------------------------------------------------------- 피처는 과거만 본다
def test_features_at_t_do_not_change_when_future_data_changes(market):
    prices, index, macro = market
    cutoff = pd.Timestamp(sorted(prices["date"].unique())[400])
    base = build_features(prices, index, macro)

    rng = np.random.default_rng(0)
    p2, i2, m2 = prices.copy(), index.copy(), macro.copy()
    fut = p2["date"] > cutoff
    for col in ("open", "high", "low", "close", "volume", "value", "market_cap"):
        p2.loc[fut, col] = p2.loc[fut, col] * rng.uniform(0.2, 5.0, fut.sum())  # 미래를 마구 바꾼다
    p2.loc[fut, ["foreign_net", "inst_net"]] = np.nan
    i2.loc[i2["date"] > cutoff, "close"] *= 3
    m2.loc[m2["date"] > cutoff, "value"] *= 10
    changed = build_features(p2, i2, m2)

    key = ["code", "date"]
    past_a = base[base["date"] <= cutoff].sort_values(key)[FEATURES]
    past_b = changed[changed["date"] <= cutoff].sort_values(key)[FEATURES]
    assert _same(past_a, past_b), "기준일 이전 피처가 미래 데이터에 따라 바뀌었다 (누수)"
    assert not _same(
        base[base["date"] > cutoff].sort_values(key)[FEATURES],
        changed[changed["date"] > cutoff].sort_values(key)[FEATURES],
    )  # 대조군: 미래 피처는 당연히 바뀐다


def test_serving_features_equal_training_features(market):
    """서빙(기준일까지만 잘라서 계산) == 학습(전체 계산의 그날 행). 학습-서빙 불일치와 누수를 함께 검사."""
    prices, index, macro = market
    full = build_features(prices, index, macro)
    for as_of in sorted(prices["date"].unique())[300::97]:
        ts = pd.Timestamp(as_of)
        served = serving_frame(prices, index, macro, ts.date()).sort_values("code")
        trained = full[full["date"] == ts].sort_values("code")
        assert len(served) == 8 and _same(served[FEATURES], trained[FEATURES])


def test_targets_are_the_only_forward_looking_columns(market):
    prices, index, _ = market
    t = build_targets(prices, index)
    targets = {"ret_fwd", "kospi_fwd", "y_up", "y_out"}
    assert not targets & set(FEATURES)
    assert not targets & set(build_features(prices, index).columns)
    g = prices[prices["code"] == prices["code"].iloc[0]].sort_values("date")
    tg = t[t["code"] == g["code"].iloc[0]].sort_values("date")
    expected = g["close"].shift(-HORIZON) / g["close"] - 1  # close[t+5]/close[t]-1
    assert np.allclose(tg["ret_fwd"].to_numpy(), expected.to_numpy(), equal_nan=True)
    assert tg["ret_fwd"].tail(HORIZON).isna().all()  # 마지막 5거래일은 타깃이 없다
    assert set(tg["y_up"].dropna().unique()) <= {0.0, 1.0}


# ---------------------------------------------------------------- walk-forward 분할
def test_folds_are_time_ordered_with_embargo_and_no_overlap(market):
    prices, _, _ = market
    days = sorted(pd.to_datetime(prices["date"].unique()))
    pos = {d: i for i, d in enumerate(days)}
    folds = make_folds(prices["date"], **FOLD_KW)
    assert len(folds) >= 3
    for f in folds:
        tr0, tr1 = pos[f.train[0]], pos[f.train[1]]
        va0, va1 = pos[f.valid[0]], pos[f.valid[1]]
        te0, te1 = pos[f.test[0]], pos[f.test[1]]
        assert tr0 <= tr1 < va0 <= va1 < te0 <= te1
        # 간격 >= 예측 기간: 학습 마지막 표본의 타깃(t+5)이 검증/테스트 구간에 닿지 않는다
        assert va0 - tr1 - 1 >= FOLD_KW["embargo"] >= HORIZON
        assert te0 - va1 - 1 >= FOLD_KW["embargo"]
        assert tr1 + HORIZON < va0 and va1 + HORIZON < te0
    tests = [(f.test[0], f.test[1]) for f in folds]
    assert all(a[1] < b[0] for a, b in zip(tests, tests[1:], strict=False))  # 테스트 구간은 겹치지 않고 전진
    assert [f.train[0] for f in folds] == sorted(f.train[0] for f in folds)


def test_spec_default_windows_are_3y_3m_3m_with_5day_embargo():
    from src import walk_forward as wf

    assert (wf.TRAIN_DAYS, wf.VALID_DAYS, wf.TEST_DAYS, wf.STEP_DAYS, wf.EMBARGO) == (756, 63, 63, 63, 5)


def test_every_test_row_is_after_every_train_row(market):
    """랜덤 분할 금지: 테스트 표본은 모두 학습 표본보다 뒤다."""
    prices, _, _ = market
    dates = pd.to_datetime(prices["date"])
    for f in make_folds(prices["date"], **FOLD_KW):
        assert dates[f.mask(dates, "train")].max() < dates[f.mask(dates, "test")].min()


# ---------------------------------------------------------------- 뉴스/공시 시점 정렬
TRADING = [date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26)]


@pytest.mark.parametrize(
    "published,expected",
    [
        (datetime(2026, 10, 21, 15, 29, tzinfo=KST), date(2026, 10, 21)),  # 장 마감 전 -> 그날
        (datetime(2026, 10, 21, 15, 30, tzinfo=KST), date(2026, 10, 22)),  # 15:30 이후 -> 다음 거래일
        (datetime(2026, 10, 21, 8, 0, tzinfo=KST), date(2026, 10, 21)),  # 개장 전 -> 그날
        (datetime(2026, 10, 24, 10, 0, tzinfo=KST), date(2026, 10, 26)),  # 토요일 -> 월요일
        (datetime(2026, 10, 26, 16, 0, tzinfo=KST), None),  # 다음 거래일을 아직 모름
    ],
)
def test_news_published_after_close_maps_to_next_trading_day(published, expected):
    assert news_feature_date(published, TRADING) == expected


def test_utc_timestamps_are_converted_to_kst_first():
    from datetime import UTC

    # UTC 06:00 = KST 15:00 (마감 전) -> 그날, UTC 06:30 = KST 15:30 (마감) -> 다음 거래일
    assert news_feature_date(datetime(2026, 10, 21, 6, 0, tzinfo=UTC), TRADING) == date(2026, 10, 21)
    assert news_feature_date(datetime(2026, 10, 21, 6, 30, tzinfo=UTC), TRADING) == date(2026, 10, 22)


def test_disclosure_filed_at_rule_never_leaks_into_same_day():
    """Phase 3 공시 규칙(접수일 23:59 KST 저장)과 맞물려 공시는 항상 다음 거래일 피처가 된다."""
    from app.pipelines.collect_disclosures import to_filed_at

    assert news_feature_date(to_filed_at("20261021"), TRADING) == date(2026, 10, 22)
