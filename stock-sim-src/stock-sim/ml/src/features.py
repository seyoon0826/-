"""피처 생성 (SPEC 8.2). 학습(ml/)과 서빙(backend/app/ml/)이 이 모듈 하나를 공유한다.

누수 방지 (SPEC 8.1)
- 기준일 t 의 피처는 t 종가까지의 데이터만 쓴다: 모든 시계열 계산은 과거 방향(rolling/shift(+k))이고,
  같은 날짜의 종목 간 비교(거래대금 순위)만 단면으로 한다. 미래 방향 shift 는 build_targets() 에만 있다.
- 정규화/스케일링을 하지 않는다(모든 피처가 비율·순위라 단위가 없다). 학습 구간 밖 통계를 쓸 일이 없다.
- 뉴스/공시: published_at 이 거래일 15:30 이전이면 그날, 이후(또는 휴장일)면 다음 거래일 피처
  (news_feature_date).

수급(외국인/기관 순매수) 결측 규칙 — prices_daily 저장 규칙과 짝을 이룬다
- 저장: 거래정지일(volume == 0)은 순매수 0, 거래가 있었는데 값이 빈 날은 NULL(진짜 결측).
- 피처: 거래정지 신호는 halted_20d(최근 20거래일 중 거래량 0 인 날 수)로 따로 준다.
  foreign/inst_net_{1,5,20}d(금액/시총)는 있는 날만 합한 뒤 창 길이/유효 일수로 비율 보정하고,
  flow_coverage_{5,20}d(유효 일수/창 길이)를 함께 넣는다.
  창 안이 전부 NULL 이면 NaN 으로 둔다 (LightGBM 이 결측 방향을 학습).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

KST = ZoneInfo("Asia/Seoul")
MARKET_CLOSE = time(15, 30)
HORIZON = 5  # 예측 기간(거래일), v1 고정

PRICE_COLUMNS = [
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
# 선택 컬럼: KRX PER/PBR/배당수익률(%) — 없으면(합성 데이터, 펀드 등) 피처가 결측
VALUATION_COLUMNS = ["per", "pbr", "div_yield"]

# build_features 가 계산하는 원래 값(근거 문장에 쓴다).
RAW_FEATURES: list[str] = [
    # 수익률
    "ret_1d",
    "ret_5d",
    "ret_20d",
    "ret_60d",
    # 추세
    "ma5_gap",
    "ma20_gap",
    "ma60_gap",
    "ma20_slope",
    # 모멘텀/과열
    "rsi_14",
    "high52_pos",
    # 변동성
    "vol_20d",
    "atr14_ratio",
    # 거래량
    "vol_ratio_20d",
    "value_rank",
    # 수급 (금액/시총, 결측 비율 보정) + 커버리지 + 거래정지
    "foreign_net_1d",
    "foreign_net_5d",
    "foreign_net_20d",
    "inst_net_1d",
    "inst_net_5d",
    "inst_net_20d",
    "flow_coverage_5d",
    "flow_coverage_20d",
    "halted_20d",
    # 밸류에이션 (KRX 일별 PER/PBR/배당수익률. 그날 종가 기준이라 장 마감 후 알 수 있다)
    "earn_yield",
    "book_to_price",
    "div_yield",
    # 시장
    "kospi_ret_5d",
    "kospi_ret_20d",
    "kospi_vol_20d",
    "usdkrw_chg_5d",
    "base_rate",
    # 상대
    "rel_ret_20d",
]

MARKET_FEATURES = ["kospi_ret_5d", "kospi_ret_20d", "kospi_vol_20d", "usdkrw_chg_5d", "base_rate"]
# 종목 피처: 같은 날 종목들 사이 순위(0~1)로 바꿔 모델에 넣는다. 시장 국면에 따라 값의 수준이 통째로 움직이는
# 문제(예: 하락장에는 모든 종목의 ret_20d 가 음수)를 없애고, '다른 종목보다 어떤가'만 남긴다.
# rel_ret_20d 는 같은 날 순위가 ret_20d 와 똑같아 뺀다. value_rank 는 이미 같은 날 순위다.
STOCK_FEATURES = [f for f in RAW_FEATURES if f not in [*MARKET_FEATURES, "value_rank", "rel_ret_20d"]]
XR = "_xr"  # cross-sectional rank

# 모델 입력 (순서가 곧 입력 순서). 바꾸면 features.json 과 달라져 서버가 기동을 거부한다 (SPEC 8.4).
FEATURES: list[str] = [f + XR for f in STOCK_FEATURES] + ["value_rank", *MARKET_FEATURES]


# ---------------------------------------------------------------- 사람이 읽는 근거 문장 (FR-PR-6)
def _pct(v: float, digits: int = 1) -> str:
    return f"{v * 100:+.{digits}f}%"


def _updown(v: float, up: str = "상승", down: str = "하락") -> str:
    return up if v >= 0 else down


_RAW_LABELS: dict[str, str] = {
    "ret_1d": "최근 1거래일 {pct} {updown}",
    "ret_5d": "최근 5거래일 {pct} {updown}",
    "ret_20d": "최근 20거래일 {pct} {updown}",
    "ret_60d": "최근 60거래일 {pct} {updown}",
    "ma5_gap": "5일 이동평균 대비 {pct} {above}",
    "ma20_gap": "20일 이동평균 대비 {pct} {above}",
    "ma60_gap": "60일 이동평균 대비 {pct} {above}",
    "ma20_slope": "20일 이동평균이 5거래일간 {pct} {updown} 추세",
    "rsi_14": "RSI(14) {num0}, {rsi_zone}",
    "high52_pos": "52주 최고가의 {ratio0} 수준",
    "vol_20d": "최근 20일 일간 변동성 {ratio1}",
    "atr14_ratio": "평균 진폭(ATR14)이 종가의 {ratio1}",
    "vol_ratio_20d": "거래량이 20일 평균의 {num1}배",
    "value_rank": "거래대금 순위 백분위 {ratio0}",
    "foreign_net_1d": "전일 외국인 {buysell} (시총 대비 {pct2})",
    "foreign_net_5d": "최근 5일 외국인 {buysell} (시총 대비 {pct2})",
    "foreign_net_20d": "최근 20일 외국인 {buysell} (시총 대비 {pct2})",
    "inst_net_1d": "전일 기관 {buysell} (시총 대비 {pct2})",
    "inst_net_5d": "최근 5일 기관 {buysell} (시총 대비 {pct2})",
    "inst_net_20d": "최근 20일 기관 {buysell} (시총 대비 {pct2})",
    "flow_coverage_5d": "최근 5일 수급 데이터 확보율 {ratio0}",
    "flow_coverage_20d": "최근 20일 수급 데이터 확보율 {ratio0}",
    "halted_20d": "최근 20거래일 중 거래정지 {num0}일",
    "earn_yield": "이익수익률(1/PER) {ratio1}",
    "book_to_price": "PBR 의 역수(순자산/시총) {num2}",
    "div_yield": "배당수익률 {ratio1}",
    "kospi_ret_5d": "KOSPI 최근 5거래일 {pct} {updown}",
    "kospi_ret_20d": "KOSPI 최근 20거래일 {pct} {updown}",
    "kospi_vol_20d": "KOSPI 20일 변동성 {ratio1}",
    "usdkrw_chg_5d": "원/달러 환율 5거래일 {pct} {updown}",
    "base_rate": "기준금리 {num2}%",
    "rel_ret_20d": "최근 20거래일 KOSPI 대비 {pctp}",
}
FEATURE_LABELS: dict[str, str] = {
    **_RAW_LABELS,
    **{f + XR: _RAW_LABELS[f] + " (같은 날 종목 중 백분위 {rank})" for f in STOCK_FEATURES},
}


def label_for(feature: str, value: float | None, raw: float | None = None) -> str:
    """피처 값 -> 근거 문장. 예: ret_20d=0.083 -> '최근 20거래일 +8.3% 상승'.
    순위 피처(…_xr)는 value 가 같은 날 순위(0~1), raw 가 원래 값이다:
    ret_20d_xr=0.9, raw=0.083 -> '최근 20거래일 +8.3% 상승 (같은 날 종목 중 백분위 90)'."""
    if feature.endswith(XR):
        base = feature[: -len(XR)]
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return label_for(base, raw)
        rank = f"같은 날 종목 중 백분위 {float(value) * 100:.0f}"
        if raw is None or (isinstance(raw, float) and np.isnan(raw)):  # 원래 값을 모르면 순위만
            return f"{_RAW_LABELS.get(base, base).split(' {')[0]} {rank}"
        return f"{label_for(base, raw)} ({rank})"
    template = FEATURE_LABELS.get(feature, feature + " {num2}")
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return template.split(" {")[0] + " (데이터 없음)"
    v = float(value)
    rsi_zone = "과열 구간" if v >= 70 else ("침체 구간" if v <= 30 else "중립 구간")
    return template.format(
        pct=_pct(v),
        pct2=_pct(v, 2),
        pctp=f"{v * 100:+.1f}%p",
        updown=_updown(v),
        above=_updown(v, "위", "아래"),
        buysell=_updown(v, "순매수", "순매도"),
        rsi_zone=rsi_zone,
        ratio0=f"{v * 100:.0f}%",
        ratio1=f"{v * 100:.1f}%",
        num0=f"{v:.0f}",
        num1=f"{v:.1f}",
        num2=f"{v:.2f}",
    )


# ---------------------------------------------------------------- 뉴스 시점 정렬 (SPEC 8.1)
def news_feature_date(published_at: datetime, trading_days: Sequence[date]) -> date | None:
    """기사가 반영될 거래일. 거래일 15:30 이전 발행이면 그날, 이후(또는 휴장일)면 다음 거래일.
    trading_days 는 오름차순 거래일 목록. 다음 거래일이 목록에 없으면 None."""
    local = published_at.astimezone(KST)
    d = local.date()
    days = list(trading_days)
    i = bisect_right(days, d)  # d 보다 큰 첫 거래일 위치
    if i > 0 and days[i - 1] == d and local.time() < MARKET_CLOSE:
        return d
    return days[i] if i < len(days) else None


# ---------------------------------------------------------------- 피처
def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(loss != 0, 100.0).where(gain.notna())


def _per_code(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("date")
    c, h, lo, v = g["close"], g["high"], g["low"], g["volume"]
    out = pd.DataFrame(index=g.index)
    for n in (1, 5, 20, 60):
        out[f"ret_{n}d"] = c / c.shift(n) - 1
    mas = {n: c.rolling(n, min_periods=n).mean() for n in (5, 20, 60)}
    for n, ma in mas.items():
        out[f"ma{n}_gap"] = c / ma - 1
    out["ma20_slope"] = mas[20] / mas[20].shift(5) - 1
    out["rsi_14"] = _rsi(c)
    out["high52_pos"] = c / h.rolling(252, min_periods=60).max()
    r1 = c.pct_change()
    out["vol_20d"] = r1.rolling(20, min_periods=20).std()
    prev = c.shift(1)
    tr = pd.concat([h - lo, (h - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
    out["atr14_ratio"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / c
    out["vol_ratio_20d"] = (v / v.rolling(20, min_periods=20).mean()).replace([np.inf, -np.inf], np.nan)
    cap = g["market_cap"].where(g["market_cap"] > 0)
    for col in ("foreign_net", "inst_net"):
        x = g[col].astype(float)
        for w in (1, 5, 20):
            s = x.rolling(w, min_periods=1).sum()
            n_valid = x.rolling(w, min_periods=1).count()
            scaled = (s * w / n_valid).where(n_valid > 0)  # 결측일 비율 보정
            out[f"{col}_{w}d"] = scaled / cap
    fx = g["foreign_net"].astype(float)
    for w in (5, 20):
        out[f"flow_coverage_{w}d"] = fx.rolling(w, min_periods=1).count() / w
    out["halted_20d"] = (v == 0).astype(float).rolling(20, min_periods=1).sum()
    # PER/PBR 이 0 이하(적자·자본잠식·미산출)면 역수가 의미 없어 결측. 역수를 써서 순위가 '싼 정도'와 같은 방향이 된다
    per, pbr = g["per"].astype(float), g["pbr"].astype(float)
    out["earn_yield"] = (1 / per).where(per > 0)
    out["book_to_price"] = (1 / pbr).where(pbr > 0)
    out["div_yield"] = g["div_yield"].astype(float) / 100
    return out


def _market_features(
    index: pd.DataFrame | None, macro: pd.DataFrame | None, dates: pd.Series
) -> pd.DataFrame:
    """날짜별 시장 피처. index: (date, close) KOSPI, macro: (series, date, value)."""
    cal = pd.DataFrame({"date": sorted(pd.unique(dates))})
    if index is not None and len(index):
        k = (
            index[["date", "close"]]
            .sort_values("date")
            .drop_duplicates("date")
            .set_index("date")["close"]
            .astype(float)
        )
        mk = pd.DataFrame(
            {
                "kospi_ret_5d": k / k.shift(5) - 1,
                "kospi_ret_20d": k / k.shift(20) - 1,
                "kospi_vol_20d": k.pct_change().rolling(20, min_periods=20).std(),
            }
        )
        cal = cal.merge(mk.reset_index(), on="date", how="left")
    for col in ("kospi_ret_5d", "kospi_ret_20d", "kospi_vol_20d"):
        if col not in cal:
            cal[col] = np.nan
    for series, col in (("usdkrw", "usdkrw"), ("base_rate", "base_rate")):
        cal[col] = np.nan
        if macro is not None and len(macro):
            m = macro[macro["series"] == series][["date", "value"]].sort_values("date")
            if len(m):
                # 기준일 이전(같은 날 포함) 마지막 발표값 (as-of 정렬, 미래값 사용 금지)
                aligned = pd.merge_asof(
                    cal[["date"]],
                    m.rename(columns={"value": col}).astype({col: float}),
                    on="date",
                    direction="backward",
                )
                cal[col] = aligned[col].to_numpy()
    cal["usdkrw_chg_5d"] = cal["usdkrw"] / cal["usdkrw"].shift(5) - 1
    return cal.drop(columns=["usdkrw"])


def in_universe(df: pd.DataFrame, members: pd.DataFrame) -> pd.DataFrame:
    """시점별 유니버스(point-in-time) 필터. members: [date(그 달 첫 거래일), code] — 그 달 내내 명단으로 쓴다.
    명단은 그 달 첫 거래일 종가 기준 시총이라 그날 이후 정보를 쓰지 않는다 (생존 편향 제거, SPEC 8.1)."""
    m = members.assign(
        month=pd.to_datetime(members["date"]).dt.to_period("M"), code=members["code"].astype(str).str.zfill(6)
    )
    m = m[["month", "code"]].drop_duplicates()
    out = df.assign(month=pd.to_datetime(df["date"]).dt.to_period("M")).merge(m, on=["month", "code"])
    return out.drop(columns="month").reset_index(drop=True)


def build_features(
    prices: pd.DataFrame,
    index: pd.DataFrame | None = None,
    macro: pd.DataFrame | None = None,
    members: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """prices(PRICE_COLUMNS) -> [code, date, close] + FEATURES. 날짜는 datetime64 로 맞춘다.
    members 를 주면(학습용 확장 유니버스) 종목별 시계열 피처는 전체 이력으로 계산하고,
    같은 날 종목 간 순위(value_rank, _xr)는 그날 명단에 든 종목들 안에서만 계산한다.
    서빙은 members 없이 서비스 종목 전체(active) 안에서 순위를 낸다."""
    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"])
    for col in VALUATION_COLUMNS:
        if col not in p:
            p[col] = np.nan
    for col in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "value",
        "foreign_net",
        "inst_net",
        "market_cap",
        *VALUATION_COLUMNS,
    ):
        p[col] = pd.to_numeric(p[col], errors="coerce").astype(float)
    p = p.sort_values(["code", "date"]).reset_index(drop=True)
    feats = p.groupby("code", group_keys=False)[p.columns].apply(_per_code)
    out = pd.concat([p[["code", "date", "close", "value"]], feats], axis=1)
    idx = index.assign(date=pd.to_datetime(index["date"])) if index is not None else None
    mac = macro.assign(date=pd.to_datetime(macro["date"])) if macro is not None else None
    mkt = _market_features(idx, mac, out["date"])
    out = out.merge(mkt, on="date", how="left")
    if members is not None:
        out = in_universe(out, members)
    out["value_rank"] = out.groupby("date")["value"].rank(pct=True)  # 같은 날 종목 간 거래대금 순위
    out["rel_ret_20d"] = out["ret_20d"] - out["kospi_ret_20d"]
    for f in STOCK_FEATURES:  # 같은 날 종목들 사이 순위 (같은 날짜 안에서만 비교 -> 미래 정보 없음)
        out[f + XR] = out.groupby("date")[f].rank(pct=True)
    return out[["code", "date", "close", *RAW_FEATURES, *(f + XR for f in STOCK_FEATURES)]]


def build_targets(
    prices: pd.DataFrame,
    index: pd.DataFrame | None,
    horizon: int = HORIZON,
    members: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """학습용 타깃 (SPEC 8.3). 이 함수만 미래(t+h)를 본다 — 피처에는 절대 쓰지 않는다.
    ret_fwd: close[t+h]/close[t]-1, y_up = 1[ret_fwd > 0], y_out = 1[ret_fwd - kospi_fwd > 0],
    y_rel = 1[ret_fwd > 같은 날 종목들의 ret_fwd 중앙값] (상승확률 모델의 학습 라벨, 보정은 y_up 으로)."""
    p = prices[["code", "date", "close"]].copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p.sort_values(["code", "date"])
    p["ret_fwd"] = p.groupby("code")["close"].shift(-horizon) / p["close"] - 1
    if index is not None and len(index):
        k = index[["date", "close"]].assign(date=lambda d: pd.to_datetime(d["date"])).sort_values("date")
        k["kospi_fwd"] = k["close"].astype(float).shift(-horizon) / k["close"].astype(float) - 1
        p = p.merge(k[["date", "kospi_fwd"]], on="date", how="left")
    else:
        p["kospi_fwd"] = np.nan
    p["y_up"] = (p["ret_fwd"] > 0).astype(float).where(p["ret_fwd"].notna())
    excess = p["ret_fwd"] - p["kospi_fwd"]
    p["y_out"] = (excess > 0).astype(float).where(excess.notna())
    if members is not None:  # 상대 라벨의 중앙값은 그날 명단 안에서
        p = in_universe(p, members)
    med = p.groupby("date")["ret_fwd"].transform("median")
    p["y_rel"] = (p["ret_fwd"] > med).astype(float).where(p["ret_fwd"].notna())
    return p[["code", "date", "ret_fwd", "kospi_fwd", "y_up", "y_out", "y_rel"]]
