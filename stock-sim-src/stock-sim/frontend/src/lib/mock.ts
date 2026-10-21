// 디자인 확인용 모의 데이터. 백엔드 API(Phase 6~7)가 생기면 api.ts 호출로 교체한다.
import type { Candle, ChartMarker, Prediction, StockSummary, TrackRecordSummary } from "@/types";

function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
}

function addBusinessDays(start: Date, n: number): Date {
  const d = new Date(start);
  let left = n;
  while (left > 0) {
    d.setDate(d.getDate() + 1);
    if (d.getDay() !== 0 && d.getDay() !== 6) left -= 1;
  }
  return d;
}

export const ymd = (d: Date) => d.toISOString().slice(0, 10);

export const AS_OF = "2026-10-20";

export const STOCKS: { code: string; name: string; price: number; chg: number; p_up: number }[] = [
  { code: "005930", name: "삼성전자", price: 72100, chg: 1.27, p_up: 0.58 },
  { code: "000660", name: "SK하이닉스", price: 198500, chg: -0.8, p_up: 0.51 },
  { code: "373220", name: "LG에너지솔루션", price: 372000, chg: 0.54, p_up: 0.47 },
  { code: "207940", name: "삼성바이오로직스", price: 1021000, chg: 2.1, p_up: 0.62 },
  { code: "005380", name: "현대차", price: 241500, chg: -1.43, p_up: 0.44 },
  { code: "000270", name: "기아", price: 118300, chg: 0.25, p_up: 0.55 },
  { code: "105560", name: "KB금융", price: 94200, chg: 0.96, p_up: 0.53 },
  { code: "035420", name: "NAVER", price: 214000, chg: -0.37, p_up: 0.49 },
];

export function stockList(): StockSummary[] {
  return STOCKS.map((s) => ({
    code: s.code,
    name: s.name,
    price: s.price,
    change_pct: s.chg,
    p_up: s.p_up,
    as_of_date: AS_OF,
  }));
}

export function findStock(code: string) {
  return STOCKS.find((s) => s.code === code);
}

export function candles(code: string, lastClose: number, n = 120): Candle[] {
  const rand = rng(Number(code) % 1000 || 7);
  const end = new Date(`${AS_OF}T00:00:00Z`);
  const days: Date[] = [];
  const d = new Date(end);
  while (days.length < n) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) days.unshift(new Date(d));
    d.setUTCDate(d.getUTCDate() - 1);
  }
  let p = lastClose * 0.88;
  const raw = days.map((day) => {
    const o = p;
    const c = o * (1 + (rand() - 0.46) * 0.026);
    const h = Math.max(o, c) * (1 + rand() * 0.008);
    const l = Math.min(o, c) * (1 - rand() * 0.008);
    p = c;
    return { day, o, c, h, l, v: 4e6 + rand() * 1.2e7 };
  });
  const k = lastClose / raw[raw.length - 1].c;
  return raw.map((r) => ({
    time: ymd(r.day),
    open: Math.round(r.o * k),
    high: Math.round(r.h * k),
    low: Math.round(r.l * k),
    close: Math.round(r.c * k),
    volume: Math.round(r.v),
  }));
}

/** 미래 5거래일 날짜 (예측 콘용) */
export function forecastDates(): string[] {
  const start = new Date(`${AS_OF}T00:00:00Z`);
  return [1, 2, 3, 4, 5].map((i) => ymd(addBusinessDays(start, i)));
}

export function markers(cs: Candle[]): ChartMarker[] {
  const pick = (i: number, type: ChartMarker["type"], title: string): ChartMarker => ({
    date: cs[cs.length - 1 - i].time,
    type,
    title,
    url: "#",
  });
  return [
    pick(38, "news", "반도체 업황 개선 기대 관련 기사"),
    pick(22, "disclosure", "분기보고서 제출"),
    pick(9, "news", "외국인 순매수 확대 관련 기사"),
  ];
}

export function prediction(code: string): Prediction {
  const s = findStock(code) ?? STOCKS[0];
  const q = { q10: -0.021, q50: 0.009, q90: 0.043 };
  const tick = (v: number) => Math.round(v / 100) * 100;
  return {
    code: s.code,
    as_of_date: AS_OF,
    horizon: 5,
    close: s.price,
    p_up: s.p_up,
    p_outperform: Math.max(0.3, s.p_up - 0.05),
    range: {
      ...q,
      price_low: tick(s.price * (1 + q.q10)),
      price_mid: tick(s.price * (1 + q.q50)),
      price_high: tick(s.price * (1 + q.q90)),
    },
    factors: [
      { rank: 1, feature: "foreign_net_5d", label: "최근 5일 외국인 순매수 지속", shap: 0.031, direction: "+" },
      { rank: 2, feature: "ret_20d", label: "최근 20거래일 +8.3% 상승", shap: 0.022, direction: "+" },
      { rank: 3, feature: "rsi_14", label: "RSI 72, 단기 과열 구간", shap: -0.016, direction: "-" },
      { rank: 4, feature: "vol_ratio_20d", label: "거래량이 20일 평균의 1.4배", shap: 0.013, direction: "+" },
      { rank: 5, feature: "usdkrw_chg_5d", label: "원/달러 환율 5일 +1.2% 상승", shap: -0.008, direction: "-" },
    ],
    news: [
      { title: "반도체 업황 개선 기대, 외국인 매수세 이어져", url: "#", published_at: "2026-10-20T09:12:00+09:00" },
      { title: "주요 기업 분기 실적 발표 앞두고 관망 분위기", url: "#", published_at: "2026-10-19T16:40:00+09:00" },
      { title: "원/달러 환율 상승, 수출주 영향 주목", url: "#", published_at: "2026-10-19T11:05:00+09:00" },
    ],
    scenarios: {
      summary: "관련 요인은 상승 쪽이 우세하지만 단기 과열 지표가 함께 나타납니다.",
      bull: "외국인 수급이 이어지면 상단 범위 부근까지 열려 있습니다. 과거 유사 사례에서는 거래량 증가가 동반됐습니다.",
      base: "중앙 예측 부근에서 완만한 흐름이 관련 요인과 함께 나타납니다.",
      bear: "과열 지표와 환율 부담이 겹치면 하단 범위를 시험할 수 있습니다.",
      generated_by: "llm",
    },
    model_version: "lgbm-20261015-a1b2c3",
  };
}

export function trackRecord(): TrackRecordSummary {
  return {
    hits: [1, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1].map(Boolean),
    direction_accuracy: 0.65,
    baseline_accuracy: 0.55,
    range_coverage: 0.78,
    n: 20,
  };
}
