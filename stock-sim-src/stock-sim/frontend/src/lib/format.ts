export const won = (n: number) => n.toLocaleString("ko-KR");

export const pct = (ratio: number, digits = 2, signed = true) => {
  const v = (ratio * 100).toFixed(digits);
  return signed && ratio > 0 ? `+${v}%` : `${v}%`;
};

export const pct0 = (ratio: number) => `${Math.round(ratio * 100)}%`;

/** 국내 관례: 상승 빨강, 하락 파랑 */
export const tone = (n: number) => (n > 0 ? "up" : n < 0 ? "dn" : "");

export const arrow = (n: number) => (n > 0 ? "▲" : n < 0 ? "▼" : "–");
