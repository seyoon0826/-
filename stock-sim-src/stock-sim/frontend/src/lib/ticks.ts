// 호가 단위 (SPEC 7.3). 백엔드 app/services/ticks.py 와 같은 표.
const TABLE: [number | null, number][] = [
  [2_000, 1],
  [5_000, 5],
  [20_000, 10],
  [50_000, 50],
  [200_000, 100],
  [500_000, 500],
  [null, 1_000],
];

export function tickSize(price: number): number {
  for (const [upper, tick] of TABLE) if (upper === null || price < upper) return tick;
  return 1_000;
}

export const isValidTick = (p: number) => p > 0 && p % tickSize(p) === 0;
export const stepUp = (p: number) => p + tickSize(p);
export const stepDown = (p: number) => Math.max(1, p - tickSize(p - 1));
export const roundToTick = (p: number) => Math.round(p / tickSize(p)) * tickSize(p);

/** 기준가(전일 종가) 대비 가격제한폭 ±30% (SPEC 7.2). 상한은 틱 내림, 하한은 틱 올림 — 백엔드와 같은 규칙. */
export function priceLimits(base: number): [number, number] {
  const upRaw = Math.floor((base * 130) / 100);
  const lowRaw = Math.ceil((base * 70) / 100);
  const up = Math.floor(upRaw / tickSize(upRaw)) * tickSize(upRaw);
  const low = Math.ceil(lowRaw / tickSize(lowRaw)) * tickSize(lowRaw);
  return [low, up];
}
