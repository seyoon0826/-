export const won = (n: number) => n.toLocaleString("ko-KR");

export const pct = (ratio: number, digits = 2, signed = true) => {
  const v = (ratio * 100).toFixed(digits);
  return signed && ratio > 0 ? `+${v}%` : `${v}%`;
};

export const pct0 = (ratio: number) => `${Math.round(ratio * 100)}%`;

/** 국내 관례: 상승 빨강, 하락 파랑 */
export const tone = (n: number) => (n > 0 ? "up" : n < 0 ? "dn" : "");

export const arrow = (n: number) => (n > 0 ? "▲" : n < 0 ? "▼" : "–");

/** 천단위 콤마 + 원 */
export const krw = (n: number) => `${won(n)}원`;

/** 등락률(% 값, 1.27 = +1.27%) 표시 */
export const pctValue = (v: number, digits = 2) => `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;

export const kstDateTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", hour12: false }) : "-";

export const STATUS_LABEL: Record<string, string> = {
  PENDING: "대기",
  PARTIAL: "부분체결",
  FILLED: "체결",
  CANCELED: "취소",
  EXPIRED: "만료",
  REJECTED: "거부",
};

export const REJECT_LABEL: Record<string, string> = {
  UNKNOWN_SYMBOL: "거래할 수 없는 종목",
  INVALID_QTY: "수량 오류",
  INVALID_TICK: "호가 단위 오류",
  PRICE_OUT_OF_LIMIT: "가격제한폭 초과",
  INSUFFICIENT_CASH: "현금 부족",
  INSUFFICIENT_QTY: "수량 부족",
  UPSTREAM_UNAVAILABLE: "시세 지연",
};
