// API 응답 타입 (SPEC 10장). 실제 API 연동(Phase 7) 시 이 타입에 맞춘다.

export interface Candle {
  time: string; // YYYY-MM-DD
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** GET /stocks/{code}/chart */
export interface ChartResponse {
  candles: { date: string; open: number; high: number; low: number; close: number; volume: number }[];
  markers: ChartMarker[];
}

/** GET /stocks/{code}/quote */
export interface QuoteResponse {
  price: number;
  change: number;
  change_pct: number;
  volume: number;
  ts: string;
  source: "pykrx" | "kis" | "replay";
}

export interface ChartMarker {
  date: string;
  type: "news" | "disclosure";
  title: string;
  url: string;
}

export interface StockSummary {
  code: string;
  name: string;
  price: number | null;
  change_pct: number | null;
  p_up: number | null;
  as_of_date: string | null;
}

export interface Factor {
  rank: number;
  feature: string;
  label: string;
  shap: number;
  direction: "+" | "-";
}

export interface NewsItem {
  title: string;
  url: string;
  published_at: string;
}

export interface Scenarios {
  summary: string;
  bull: string;
  base: string;
  bear: string;
  generated_by: "llm" | "template";
}

export interface Prediction {
  code: string;
  as_of_date: string;
  horizon: number;
  close: number;
  p_up: number;
  p_outperform: number;
  range: {
    q10: number;
    q50: number;
    q90: number;
    price_low: number;
    price_mid: number;
    price_high: number;
  };
  factors: Factor[];
  news: NewsItem[];
  scenarios: Scenarios;
  model_version: string;
}

export interface TrackRecordSummary {
  hits: boolean[]; // 최근 예측 순서대로 방향 적중 여부
  direction_accuracy: number;
  baseline_accuracy: number; // 항상 상승 베이스라인
  range_coverage: number; // q10~q90 적중률 (목표 0.8)
  n: number;
}

export const DISCLAIMER = "본 서비스는 교육·모의투자 목적이며 예측은 투자 권유가 아닙니다.";
