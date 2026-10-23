// 백엔드 API 응답 타입 (SPEC 10, 11). 백엔드 스키마와 이름을 그대로 맞춘다.

export const DISCLAIMER = "본 서비스는 교육·모의투자 목적이며 예측은 투자 권유가 아닙니다.";

// ---------------------------------------------------------------- 시세 (10.1)
export interface StockSummary {
  code: string;
  name: string;
  price: number | null;
  change_pct: number | null;
  p_up: number | null;
  as_of_date: string | null;
}

export interface Candle {
  time: string; // YYYY-MM-DD (차트 라이브러리 형식)
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ChartMarker {
  date: string;
  type: "news" | "disclosure";
  title: string;
  url: string;
}

export interface ChartResponse {
  candles: { date: string; open: number; high: number; low: number; close: number; volume: number }[];
  markers: ChartMarker[];
}

export interface QuoteResponse {
  price: number;
  change: number;
  change_pct: number;
  volume: number;
  ts: string;
  source: "pykrx" | "kis" | "replay";
}

export interface BookLevel {
  price: number;
  qty: number;
}

export interface OrderbookResponse {
  asks: BookLevel[];
  bids: BookLevel[];
  ts: string;
  source: string;
}

// ---------------------------------------------------------------- 예측 (10.2)
export interface Factor {
  rank: number;
  feature: string;
  label: string;
  value: number | null;
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

export interface PriceRange {
  q10: number;
  q50: number;
  q90: number;
  price_low: number;
  price_mid: number;
  price_high: number;
}

export interface Prediction {
  code: string;
  as_of_date: string;
  horizon: number;
  close: number;
  p_up: number;
  p_outperform: number | null;
  range: PriceRange;
  factors: Factor[];
  news: NewsItem[];
  scenarios: Scenarios | null;
  model_version: string;
}

export interface Outcome {
  target_date: string;
  actual_return: number;
  actual_excess: number | null;
  hit_direction: boolean;
  in_range: boolean;
}

export interface HistoryItem {
  as_of_date: string;
  close: number;
  p_up: number;
  range: PriceRange;
  model_version: string;
  outcome: Outcome | null;
}

/** 오프라인 walk-forward 예측력 (예측 API meta.skill). level=low 이면 '예측력 낮음' 표시. */
export interface ModelSkill {
  scope: "offline";
  brier_skill: number; // 1 - Brier(모델)/Brier(과거 상승 비율). 0 이하면 기준선보다 낫지 않다
  ic: number;
  ic_positive_folds: number;
  folds: number;
  level: "low" | "ok";
}

export interface HistorySummary {
  n: number;
  hit_rate: number | null;
  range_coverage: number | null;
}

export type Strategy = "model" | "always_up" | "momentum" | "kospi";

export interface StrategyMetrics {
  n: number;
  direction_accuracy?: number | null;
  brier?: number | null;
  pinball_q10?: number | null;
  pinball_q50?: number | null;
  pinball_q90?: number | null;
  range_coverage?: number | null;
  ic?: number | null;
}

export interface CalibrationBin {
  bin: number;
  lo: number;
  hi: number;
  p_mean: number | null;
  actual: number | null;
  n: number;
}

export interface Performance {
  model_version: string;
  scope: "live" | "offline";
  window: string;
  strategies: Partial<Record<Strategy, StrategyMetrics>>;
  best: Record<string, Strategy>;
  calibration_up: CalibrationBin[];
  daily: { date: string; hit_rate: number; cumulative_hit_rate: number; n: number }[];
  range_coverage_target: number;
  n: number;
}

// ---------------------------------------------------------------- 모의투자 (10.4)
export type OrderStatus = "PENDING" | "PARTIAL" | "FILLED" | "CANCELED" | "EXPIRED" | "REJECTED";

export interface Order {
  id: string;
  code: string;
  side: "BUY" | "SELL";
  order_type: "MARKET" | "LIMIT";
  qty: number;
  filled_qty: number;
  limit_price: number | null;
  reserved_amount: number;
  status: OrderStatus;
  reject_reason: string | null;
  queued: boolean;
  source: "USER" | "AI";
  prediction_id?: number | null;
  basis?: { p_up: number; as_of_date: string } | null; // AI 주문의 근거 예측 (FR-AI-4)
  created_at: string | null;
  updated_at: string | null;
  trades?: Trade[];
}

export interface Trade {
  id: number;
  order_id: string;
  code: string;
  side: "BUY" | "SELL";
  qty: number;
  price: number;
  amount: number;
  fee: number;
  tax: number;
  filled_at: string | null;
}

export interface Holding {
  code: string;
  qty: number;
  reserved_qty: number;
  avg_price: number;
  price: number;
  value: number;
  pnl: number;
  return_pct: number;
}

export interface Portfolio {
  account_id: number;
  type: "USER" | "AI";
  holdings: Holding[];
  cash: number;
  reserved_cash: number;
  available_cash: number;
  stock_value: number;
  total_value: number;
  initial_cash: number;
  return_pct: number;
}

export interface Accounts {
  user: Portfolio;
  ai: Portfolio;
}

export interface Estimate {
  code: string;
  qty: number;
  immediately_fillable_qty: number;
  avg_price: number | null;
  amount: number;
  fee: number;
  tax: number;
  total: number;
  required_reserve: number | null;
  rates: { buy_fee: string; sell_fee: string; sell_tax: string };
}

export interface PaperPerformance {
  account_id: number;
  type: "USER" | "AI";
  curve: { date: string; total_value: number; return_pct: number; kospi_return_pct: number | null }[];
  mdd_pct: number;
  win_rate: number | null;
  sell_count: number;
  trade_count: number;
  turnover: number;
}

// ---------------------------------------------------------------- WebSocket (11)
export type WsQuote = { type: "quote"; code: string; price: number; change_pct: number; volume: number; ts: string };
export type WsBook = { type: "orderbook"; code: string; asks: BookLevel[]; bids: BookLevel[]; ts: string };
export type WsStatusMsg = { type: "status"; market: "open" | "closed"; provider: string; degraded: boolean };
