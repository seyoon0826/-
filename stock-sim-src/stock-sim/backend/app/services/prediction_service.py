"""예측 생성·결과 채우기·성적표 지표 (SPEC 6.3 FR-PR, 6.5 FR-PF).

daily_batch 가 이 순서로 부른다 (SPEC 12, 15:40):
결과 채우기 -> 피처 -> 예측 -> SHAP -> 뉴스 -> 시나리오 -> 지표.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ml import evaluate as ev
from app.ml.explain import Explainer
from app.ml.features import HORIZON, news_feature_date
from app.ml.infer import ModelBundle, load_inputs, serving_frame
from app.models import (
    IndexDaily,
    MarketCalendar,
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
from app.pipelines.common import JobResult, SessionFactory, job_run
from app.services.market_data import KST
from app.services.scenario_service import LLMClient, generate_scenario
from app.services.ticks import round_to_tick

log = logging.getLogger(__name__)

NEWS_DAYS, NEWS_MIN_RELEVANCE, NEWS_MAX = 3, Decimal("0.60"), 3  # FR-PR-7
WINDOWS = ("20", "60", "250", "all")  # FR-PF-3
METRIC_KEYS = (
    "direction_accuracy",
    "brier",
    "pinball_q10",
    "pinball_q50",
    "pinball_q90",
    "range_coverage",
    "ic",
)


def _pct(v: float) -> str:
    return f"{v * 100:+.1f}%"


def _won(v: int) -> str:
    return f"{v:,}원"


def price_range(close: int, q10: float, q50: float, q90: float) -> dict[str, int]:
    """예상 가격 범위: close x (1+q) 를 호가 단위로 반올림 (FR-PR-4)."""
    return {
        "price_low": round_to_tick(close * (1 + q10)),
        "price_mid": round_to_tick(close * (1 + q50)),
        "price_high": round_to_tick(close * (1 + q90)),
    }


def trading_days(session: Session, end: date, n: int) -> list[date]:
    """end 이하 최근 n 거래일 (캘린더 우선, 없으면 가격 데이터 날짜)."""
    days = list(
        session.scalars(
            select(MarketCalendar.date)
            .where(MarketCalendar.is_open.is_(True), MarketCalendar.date <= end)
            .order_by(MarketCalendar.date.desc())
            .limit(n)
        )
    )
    if len(days) < n:
        days = list(
            session.scalars(
                select(PriceDaily.date)
                .where(PriceDaily.date <= end)
                .distinct()
                .order_by(PriceDaily.date.desc())
                .limit(n)
            )
        )
    return sorted(days)


def related_news(session: Session, code: str, as_of: date) -> list[News]:
    """기준일 직전 3거래일(기준일 포함)에 반영되는 관련도 0.6 이상 기사, 최대 3건.
    (FR-PR-7, 시점 정렬은 SPEC 8.1)"""
    window = trading_days(session, as_of, NEWS_DAYS)
    if not window:
        return []
    start = datetime.combine(window[0] - timedelta(days=7), time.min, tzinfo=KST)
    end = datetime.combine(as_of, time(15, 30), tzinfo=KST)  # 기준일 15:30 이후 기사는 다음 거래일 피처
    rows = session.execute(
        select(News, NewsStockMap.relevance)
        .join(NewsStockMap, NewsStockMap.news_id == News.id)
        .where(
            NewsStockMap.code == code,
            NewsStockMap.relevance >= NEWS_MIN_RELEVANCE,
            News.published_at >= start,
            News.published_at < end,
        )
        .order_by(NewsStockMap.relevance.desc(), News.published_at.desc())
    ).all()
    calendar = trading_days(session, as_of, 30)
    picked = [n for n, _ in rows if news_feature_date(n.published_at, calendar) in window]
    return picked[:NEWS_MAX]


def market_context(features: pd.Series, macro: pd.DataFrame) -> dict[str, str]:
    ctx: dict[str, str] = {}
    if pd.notna(features.get("kospi_ret_5d")):
        ctx["KOSPI_5일_수익률"] = _pct(float(features["kospi_ret_5d"]))
    for series, key, fmt in (("usdkrw", "원달러_환율", "{:,.1f}원"), ("base_rate", "기준금리", "{:.2f}%")):
        m = macro[macro["series"] == series] if len(macro) else macro
        if len(m):
            ctx[key] = fmt.format(float(m.sort_values("date")["value"].iloc[-1]))
    return ctx


def scenario_input(
    name: str,
    as_of: date,
    close: int,
    pred: pd.Series,
    factors: list[dict[str, Any]],
    news: list[News],
    market: dict[str, str],
) -> dict[str, Any]:
    """FR-SC-1 입력 JSON. 숫자는 화면 표기 그대로의 문자열로 넣는다."""
    pr = price_range(close, pred["q10"], pred["q50"], pred["q90"])
    return {
        "종목명": name,
        "기준일": as_of.isoformat(),
        "기준일_종가": _won(close),
        "예측": {
            "5거래일_상승확률": f"{pred['p_up'] * 100:.0f}%",
            "KOSPI_초과확률": f"{pred['p_outperform'] * 100:.0f}%",
            "예상_수익률_범위": {
                "하단": _pct(pred["q10"]),
                "중앙": _pct(pred["q50"]),
                "상단": _pct(pred["q90"]),
            },
            "예상_가격_범위": {
                "하단": _won(pr["price_low"]),
                "중앙": _won(pr["price_mid"]),
                "상단": _won(pr["price_high"]),
            },
        },
        "근거_TOP5": [f["label"] for f in factors],
        "관련_뉴스": [
            {"제목": n.title, "날짜": n.published_at.astimezone(KST).date().isoformat()} for n in news
        ],
        "시장_상황": market,
    }


# ---------------------------------------------------------------- 예측 생성 (FR-PR-1~9)
@dataclass
class BatchContext:
    bundle: ModelBundle
    explainer: Explainer
    llm: LLMClient | None


def predict_day(session_factory: SessionFactory, ctx: BatchContext, as_of: date, res: JobResult) -> int:
    """기준일 하루치 예측. 종목별 트랜잭션.
    이미 있는 (종목, 기준일, 호라이즌, 모델버전)은 건너뛴다 (FR-PR-9)."""
    with session_factory() as s:
        prices, index, macro = load_inputs(s, as_of)
        names = dict(s.execute(select(Stock.code, Stock.name).where(Stock.active.is_(True))).all())
        done = set(
            s.scalars(
                select(Prediction.code).where(
                    Prediction.as_of_date == as_of,
                    Prediction.horizon == HORIZON,
                    Prediction.model_version == ctx.bundle.version,
                )
            )
        )
    X = serving_frame(prices, index, macro, as_of)
    X = X[X["code"].isin(names) & ~X["code"].isin(done)].reset_index(drop=True)
    if X.empty:
        return 0
    preds = ctx.bundle.predict(X)
    created = 0
    for i, row in X.iterrows():
        code = row["code"]
        try:
            factors = ctx.explainer.top_factors(X.iloc[[i]])[0]
            p = preds.loc[i]
            close = int(row["close"])
            with session_factory() as s:
                news = related_news(s, code, as_of)
                inp = scenario_input(names[code], as_of, close, p, factors, news, market_context(row, macro))
                sc = generate_scenario(inp, ctx.llm)
                stmt = (
                    insert(Prediction)
                    .values(
                        code=code,
                        as_of_date=as_of,
                        horizon=HORIZON,
                        close_at_asof=close,
                        p_up=Decimal(f"{p['p_up']:.6f}"),
                        p_outperform=Decimal(f"{p['p_outperform']:.6f}"),
                        q10=Decimal(f"{p['q10']:.6f}"),
                        q50=Decimal(f"{p['q50']:.6f}"),
                        q90=Decimal(f"{p['q90']:.6f}"),
                        model_version=ctx.bundle.version,
                    )
                    .on_conflict_do_nothing(constraint="uq_predictions_key")
                    .returning(Prediction.id)
                )
                pid = s.scalar(stmt)
                if pid is None:  # 다른 실행이 먼저 저장함 (FR-PR-9: 덮어쓰지 않는다)
                    s.rollback()
                    continue
                for f in factors:
                    s.add(
                        PredictionFactor(
                            prediction_id=pid,
                            rank=f["rank"],
                            feature=f["feature"],
                            value=None if f["value"] is None else Decimal(f"{f['value']:.8f}"),
                            shap_value=Decimal(f"{f['shap_value']:.8f}"),
                            label=f["label"],
                        )
                    )
                for n in news:
                    s.add(PredictionNews(prediction_id=pid, news_id=n.id))
                s.add(
                    PredictionScenario(
                        prediction_id=pid,
                        summary=sc.summary,
                        bull=sc.bull,
                        base=sc.base,
                        bear=sc.bear,
                        generated_by=sc.generated_by,
                        llm_model=sc.llm_model,
                        prompt_version=sc.prompt_version,
                        input_tokens=sc.input_tokens,
                        output_tokens=sc.output_tokens,
                    )
                )
                s.commit()
            created += 1
            res.ok += 1
        except Exception as e:  # 일부 종목 실패해도 계속 (SPEC 6.2 FR-CL-6 과 같은 원칙)
            log.exception("%s 예측 실패", code)
            res.failed.append(f"{as_of} {code}: {e.__class__.__name__}")
    return created


# ---------------------------------------------------------------- 결과 채우기 (FR-PF-1)
def fill_outcomes(session: Session) -> int:
    """기준일로부터 5거래일이 지난 예측에 실제 결과를 채운다.
    가격은 지금 DB 의 값(수정주가)으로 다시 읽는다."""
    pending = session.execute(
        select(Prediction)
        .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(PredictionOutcome.prediction_id.is_(None))
    ).scalars()
    n = 0
    for p in pending:
        rows = session.execute(
            select(PriceDaily.date, PriceDaily.close)
            .where(PriceDaily.code == p.code, PriceDaily.date >= p.as_of_date)
            .order_by(PriceDaily.date)
            .limit(p.horizon + 1)
        ).all()
        if len(rows) < p.horizon + 1 or rows[0].date != p.as_of_date:
            continue  # 아직 5거래일이 지나지 않음
        (d0, c0), (d1, c1) = rows[0], rows[p.horizon]
        actual = c1 / c0 - 1
        k = dict(
            session.execute(
                select(IndexDaily.date, IndexDaily.close).where(
                    IndexDaily.index_code == "KOSPI", IndexDaily.date.in_([d0, d1])
                )
            ).all()
        )
        excess = actual - (float(k[d1]) / float(k[d0]) - 1) if d0 in k and d1 in k else None
        session.add(
            PredictionOutcome(
                prediction_id=p.id,
                target_date=d1,
                actual_return=Decimal(f"{actual:.6f}"),
                actual_excess=None if excess is None else Decimal(f"{excess:.6f}"),
                hit_direction=(float(p.p_up) >= 0.5) == (actual > 0),
                in_range=float(p.q10) <= actual <= float(p.q90),
            )
        )
        n += 1
    session.flush()
    return n


# ---------------------------------------------------------------- 성적표 지표 (FR-PF-2/3/5)
def _ret_n(prices: pd.DataFrame, n: int) -> pd.Series:
    """종목별 직전 n 거래일 수익률 (기준일 종가까지만, 베이스라인 신호용)."""
    p = prices.sort_values(["code", "date"])
    return p.groupby("code")["close"].transform(lambda c: c / c.shift(n) - 1)


def realized_quantiles(px: pd.DataFrame, first: date, horizon: int = HORIZON) -> tuple[float, float, float]:
    """베이스라인 범위용 무조건부 분위수(10/50/90%).
    첫 기준일 '전에 이미 결과가 확정된' 5일 수익률만 쓴다(미래 정보 없음): 날짜 d 의 5일 뒤 종가가
    first 전날까지 나와 있어야 하므로 d 는 first 이전 거래일 중 끝에서 horizon+1 번째 이하."""
    before = sorted(px.loc[px["date"] < first, "date"].unique())
    if len(before) <= horizon:
        return (-0.05, 0.0, 0.05)
    cutoff = before[-(horizon + 1)]
    r = px.loc[px["date"] <= cutoff, "fwd_5d"].dropna()
    if len(r) < 30:
        return (-0.05, 0.0, 0.05)
    q = r.quantile([0.1, 0.5, 0.9])
    return (float(q.iloc[0]), float(q.iloc[1]), float(q.iloc[2]))


def live_frame(session: Session, model_version: str) -> pd.DataFrame:
    """결과가 채워진 라이브 예측 + 베이스라인 신호 (기준일 시점의 직전 5일 수익률)."""
    rows = session.execute(
        select(
            Prediction.code,
            Prediction.as_of_date,
            Prediction.p_up,
            Prediction.q10,
            Prediction.q50,
            Prediction.q90,
            PredictionOutcome.actual_return,
        )
        .join(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(Prediction.model_version == model_version)
    ).all()
    df = pd.DataFrame(rows, columns=["code", "date", "p_up", "q10", "q50", "q90", "ret_fwd"])
    if df.empty:
        return df
    for c in ("p_up", "q10", "q50", "q90", "ret_fwd"):
        df[c] = df[c].astype(float)
    df["y_up"] = (df["ret_fwd"] > 0).astype(float)
    start = df["date"].min() - timedelta(days=500)
    end = df["date"].max()
    px = pd.DataFrame(
        session.execute(
            select(PriceDaily.code, PriceDaily.date, PriceDaily.close).where(
                PriceDaily.code.in_(df["code"].unique().tolist()), PriceDaily.date.between(start, end)
            )
        ).all(),
        columns=["code", "date", "close"],
    )
    px["ret_5d"] = _ret_n(px, 5)
    px["fwd_5d"] = (
        px.sort_values(["code", "date"]).groupby("code")["close"].transform(lambda c: c.shift(-5) / c - 1)
    )
    k = pd.DataFrame(
        session.execute(
            select(IndexDaily.date, IndexDaily.close).where(
                IndexDaily.index_code == "KOSPI", IndexDaily.date <= end
            )
        ).all(),
        columns=["date", "close"],
    ).sort_values("date")
    k["kospi_ret_5d"] = k["close"].astype(float) / k["close"].astype(float).shift(5) - 1
    df = df.merge(px[["code", "date", "ret_5d"]], on=["code", "date"], how="left")
    df = df.merge(k[["date", "kospi_ret_5d"]], on="date", how="left")
    df.attrs["uncond"] = realized_quantiles(px, df["date"].min())
    return df


def strategy_frames(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    u10, u50, u90 = df.attrs.get("uncond", (-0.05, 0.0, 0.05))

    def frame(p_up: pd.Series, center: pd.Series) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": df["date"],
                "p_up": p_up,
                "q10": center + (u10 - u50),
                "q50": center,
                "q90": center + (u90 - u50),
                "y_up": df["y_up"],
                "ret_fwd": df["ret_fwd"],
            }
        )

    ones = pd.Series(1.0, index=df.index)
    return {
        "model": df[["date", "p_up", "q10", "q50", "q90", "y_up", "ret_fwd"]],
        "always_up": frame(ones, ones * u50),
        "momentum": frame((df["ret_5d"] > 0).astype(float), df["ret_5d"].fillna(0.0)),
        "kospi": frame((df["kospi_ret_5d"] > 0).astype(float), df["kospi_ret_5d"].fillna(0.0)),
    }


def window_slice(df: pd.DataFrame, window: str) -> pd.DataFrame:
    if window == "all" or df.empty:
        return df
    days = sorted(df["date"].unique())[-int(window) :]
    return df[df["date"].isin(days)]


def _upsert_metric(
    session: Session,
    version: str,
    scope: str,
    window: str,
    strategy: str,
    metric: str,
    value: float | None,
    n: int,
    now: datetime,
) -> None:
    v = None if value is None or (isinstance(value, float) and np.isnan(value)) else Decimal(f"{value:.8f}")
    stmt = insert(ModelMetric).values(
        model_version=version,
        scope=scope,
        window=window,
        strategy=strategy,
        metric=metric,
        value=v,
        n=n,
        computed_at=now,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["model_version", "scope", "window", "strategy", "metric"],
            set_={
                "value": stmt.excluded.value,
                "n": stmt.excluded.n,
                "computed_at": stmt.excluded.computed_at,
            },
        )
    )


def recompute_live_metrics(session: Session, version: str, now: datetime) -> int:
    df = live_frame(session, version)
    if df.empty:
        return 0
    n_rows = 0
    for window in WINDOWS:
        part = window_slice(df, window)
        for strategy, frame in strategy_frames(part).items():
            m = ev.metrics(frame)
            for key in METRIC_KEYS:
                _upsert_metric(session, version, "live", window, strategy, key, m[key], m["n"], now)
                n_rows += 1
    return n_rows


def store_offline_metrics(session: Session, model_dir: str | Path, version: str, now: datetime) -> int:
    """학습 시 walk-forward 결과(metrics.json)를 scope=offline 으로 기록 (FR-PF-5: 라이브와 구분 표기)."""
    path = Path(model_dir) / version / "metrics.json"
    if not path.exists():
        return 0
    m = json.loads(path.read_text())
    n_rows = 0
    for strategy, vals in m.get("strategies", {}).items():
        for key in METRIC_KEYS:
            _upsert_metric(
                session, version, "offline", "all", strategy, key, vals.get(key), int(vals.get("n", 0)), now
            )
            n_rows += 1
    return n_rows


SKILL_MIN_POSITIVE_FOLDS = 0.6  # IC 가 양수인 fold 비율 기준
_skill_cache: dict[tuple[str, str], dict[str, Any] | None] = {}


def model_skill(model_dir: str | Path, version: str) -> dict[str, Any] | None:
    """오프라인 walk-forward 예측력 (metrics.json 의 skill). 화면 안내 문구에 쓴다.
    level
    - low     : 종목 간 순위도 맞히지 못함 (IC <= 0 이거나 IC 양수 fold 60% 미만)
    - ranking : 순위(어느 종목이 상대적으로 나을지)에는 약한 예측력,
                절대 확률은 과거 상승 비율과 비슷 (Brier skill <= 0)
    - ok      : 둘 다 기준선보다 낫다"""
    key = (str(model_dir), version)
    if key not in _skill_cache:
        path = Path(model_dir) / version / "metrics.json"
        k = json.loads(path.read_text()).get("skill") if path.exists() else None
        if k is None:
            _skill_cache[key] = None
        else:
            share = k["ic_positive_folds"] / k["folds"] if k["folds"] else 0.0
            ranks = k["ic_p_up"] > 0 and share >= SKILL_MIN_POSITIVE_FOLDS
            level = "ok" if ranks and k["brier_skill"] > 0 else ("ranking" if ranks else "low")
            _skill_cache[key] = {
                "scope": "offline",
                "brier_skill": round(float(k["brier_skill"]), 4),
                "ic": round(float(k["ic_p_up"]), 4),
                "ic_positive_folds": int(k["ic_positive_folds"]),
                "folds": int(k["folds"]),
                "level": level,
            }
    return _skill_cache[key]


_rank_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}


TIERS = (("상위권", 0.0, 0.3), ("중위권", 0.3, 0.7), ("하위권", 0.7, 1.0))  # 순위 백분위 구간


def expected_from_rank(
    model_dir: str | Path, version: str, rank: int | None, of: int | None
) -> dict[str, Any] | None:
    """그날 순위가 비슷했던 종목들이 과거에 실제로 어떻게 움직였나
    (metrics.json 의 rank_returns: walk-forward 테스트 구간, 학습에 안 쓴 기간). rank 1 = 상위.
    10구간 값은 잡음이 커서(2026-10 모델: 구간별 평균이 단조롭지 않음) 상위 30% / 중간 40% / 하위 30%
    세 묶음으로 합쳐 표본 수로 가중평균한다. 표가 없으면(예전 모델) None."""
    if rank is None or not of:
        return None
    key = (str(model_dir), version)
    if key not in _rank_cache:
        path = Path(model_dir) / version / "metrics.json"
        _rank_cache[key] = (json.loads(path.read_text()).get("rank_returns") or []) if path.exists() else []
    table = _rank_cache[key]
    if not table:
        return None
    bins = max(r["bin"] for r in table) + 1
    b = min(max(int(np.ceil(rank / of * bins)) - 1, 0), bins - 1)
    pos = (b + 0.5) / bins
    name, lo, hi = next(t for t in TIERS if t[1] <= pos < t[2] or (t[2] == 1.0 and pos >= t[1]))
    group = [
        r
        for r in table
        if lo <= (r["bin"] + 0.5) / bins < hi or (hi == 1.0 and (r["bin"] + 0.5) / bins >= lo)
    ]
    n = sum(r["n"] for r in group)
    if n == 0:
        return None
    return {
        "tier": name,
        "up_ratio": sum(r["up_ratio"] * r["n"] for r in group) / n,
        "mean_return": sum(r["mean_return"] * r["n"] for r in group) / n,
        "n": n,
        "ups": round(sum(r["up_ratio"] * r["n"] for r in group)),
    }


def calibration_live(session: Session, version: str, window: str) -> list[dict[str, Any]]:
    df = window_slice(live_frame(session, version), window)
    if df.empty:
        return ev.calibration_curve(np.array([]), np.array([]))
    return ev.calibration_curve(df["p_up"].to_numpy(), df["y_up"].to_numpy())


def daily_hit_series(session: Session, version: str, window: str) -> list[dict[str, Any]]:
    """기준일별 방향 적중률과 누적 적중률 (성적표의 일별 누적 적중률 추이, SPEC 13.4)."""
    df = window_slice(live_frame(session, version), window)
    if df.empty:
        return []
    df = df.assign(hit=((df["p_up"] >= 0.5) == (df["y_up"] == 1)).astype(float))
    g = df.groupby("date")["hit"].agg(["sum", "count"]).sort_index()
    cum = g["sum"].cumsum() / g["count"].cumsum()
    return [
        {
            "date": d.isoformat(),
            "hit_rate": float(r["sum"] / r["count"]),
            "n": int(r["count"]),
            "cumulative_hit_rate": float(cum[d]),
        }
        for d, r in g.iterrows()
    ]


def missing_days(session: Session, version: str, limit: int = 10) -> list[date]:
    """예측이 없는 최근 가격 데이터 날짜 (catch-up 대상, 오래된 순)."""
    last_pred = session.scalar(
        select(func.max(Prediction.as_of_date)).where(Prediction.model_version == version)
    )
    q = select(PriceDaily.date).distinct()
    if last_pred:
        q = q.where(PriceDaily.date > last_pred)
    days = sorted(session.scalars(q.order_by(PriceDaily.date.desc()).limit(limit)))
    return days


def run_daily_batch(
    session_factory: SessionFactory,
    ctx: BatchContext,
    model_dir: str | Path,
    as_of: date | None = None,
    now: Callable[[], datetime] | None = None,
) -> JobResult:
    """SPEC 12 15:40 배치(가격 수집 이후): 결과 채우기 -> 예측(누락일 catch-up 포함) -> 지표 재계산."""
    clock = now or (lambda: datetime.now(KST))
    with job_run("daily_batch", session_factory) as res:
        with session_factory() as s:
            filled = fill_outcomes(s)
            s.commit()
            days = [as_of] if as_of else missing_days(s, ctx.bundle.version)
        log.info("결과 채우기 %d건, 예측 대상일 %s", filled, days)
        for d in days:
            predict_day(session_factory, ctx, d, res)
        with session_factory() as s:
            fill_outcomes(s)  # 오늘 예측분은 아직 대상 아님. 과거 catch-up 예측의 결과를 채운다
            recompute_live_metrics(s, ctx.bundle.version, clock())
            store_offline_metrics(s, model_dir, ctx.bundle.version, clock())
            s.commit()
    return res


def ranks_on(session: Session, as_of: date, version: str | None) -> dict[str, tuple[int, int]]:
    """같은 기준일·같은 모델의 종목 간 상승확률 순위 {code: (순위, 종목 수)}. 1위가 가장 높다.
    보정(Platt)은 단조라 순위는 모델 점수 순위와 같다 — 이 모델의 예측력(IC)이 있는 곳."""
    q = select(Prediction.code, Prediction.p_up).where(Prediction.as_of_date == as_of)
    if version:
        q = q.where(Prediction.model_version == version)
    rows = sorted(session.execute(q).all(), key=lambda r: (-r.p_up, r.code))
    return {r.code: (i, len(rows)) for i, r in enumerate(rows, start=1)}


def latest_prediction(session: Session, code: str, version: str | None = None) -> Prediction | None:
    q = select(Prediction).where(Prediction.code == code)
    if version:
        q = q.where(Prediction.model_version == version)
    return session.scalars(
        q.order_by(Prediction.as_of_date.desc(), Prediction.created_at.desc()).limit(1)
    ).first()
