import type { CalibrationBin } from "@/types";

const W = 320;
const H = 220;
const P = 32;
const sx = (v: number) => P + v * (W - 2 * P);
const sy = (v: number) => H - P - v * (H - 2 * P);

/** 보정 곡선: 예측 P(up) 구간별 실제 상승 비율, 대각선 = 완벽한 보정 (FR-PF-4) */
export function CalibrationChart({ bins }: { bins: CalibrationBin[] }) {
  const pts = bins.filter((b) => b.p_mean != null && b.actual != null && b.n > 0);
  const maxN = Math.max(1, ...pts.map((b) => b.n));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" style={{ maxHeight: 260 }}>
      <title>보정 곡선</title>
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={t}>
          <line x1={sx(0)} x2={sx(1)} y1={sy(t)} y2={sy(t)} stroke="var(--border)" />
          <text x={P - 6} y={sy(t) + 3} fontSize="12" textAnchor="end" fill="var(--text-2)">
            {Math.round(t * 100)}%
          </text>
          <text x={sx(t)} y={H - P + 14} fontSize="12" textAnchor="middle" fill="var(--text-2)">
            {Math.round(t * 100)}%
          </text>
        </g>
      ))}
      <line x1={sx(0)} y1={sy(0)} x2={sx(1)} y2={sy(1)} stroke="var(--text-3)" strokeDasharray="4 3" />
      {pts.length > 1 && (
        <polyline
          fill="none"
          stroke="var(--up)"
          strokeWidth="1.5"
          points={pts.map((b) => `${sx(b.p_mean as number)},${sy(b.actual as number)}`).join(" ")}
        />
      )}
      {pts.map((b) => (
        <circle
          key={b.bin}
          cx={sx(b.p_mean as number)}
          cy={sy(b.actual as number)}
          r={2 + 5 * Math.sqrt(b.n / maxN)}
          fill="var(--up)"
          opacity="0.75"
        >
          <title>{`예측 ${Math.round((b.p_mean as number) * 100)}% → 실제 ${Math.round((b.actual as number) * 100)}% (n=${b.n})`}</title>
        </circle>
      ))}
      <text x={W / 2} y={H - 2} fontSize="12" textAnchor="middle" fill="var(--text-2)">
        예측 상승확률
      </text>
      <text x={10} y={P - 10} fontSize="12" fill="var(--text-2)">
        실제 상승 비율
      </text>
    </svg>
  );
}

/** 일별 누적 적중률 추이 (50% 기준선) */
export function HitTrend({ daily }: { daily: { date: string; cumulative_hit_rate: number; hit_rate: number }[] }) {
  if (daily.length < 2) return null;
  const x = (i: number) => P + (i / (daily.length - 1)) * (W - 2 * P);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" style={{ maxHeight: 260 }}>
      <title>누적 적중률 추이</title>
      {[0, 0.5, 1].map((t) => (
        <g key={t}>
          <line
            x1={P}
            x2={W - P}
            y1={sy(t)}
            y2={sy(t)}
            stroke={t === 0.5 ? "var(--text-3)" : "var(--border)"}
            strokeDasharray={t === 0.5 ? "4 3" : undefined}
          />
          <text x={P - 6} y={sy(t) + 3} fontSize="12" textAnchor="end" fill="var(--text-2)">
            {Math.round(t * 100)}%
          </text>
        </g>
      ))}
      {daily.map((d, i) => (
        <circle key={d.date} cx={x(i)} cy={sy(d.hit_rate)} r="1.6" fill="var(--text-3)" />
      ))}
      <polyline
        fill="none"
        stroke="var(--hit)"
        strokeWidth="2"
        points={daily.map((d, i) => `${x(i)},${sy(d.cumulative_hit_rate)}`).join(" ")}
      />
      <text x={P} y={H - 8} fontSize="12" fill="var(--text-2)">
        {daily[0].date}
      </text>
      <text x={W - P} y={H - 8} fontSize="12" textAnchor="end" fill="var(--text-2)">
        {daily[daily.length - 1].date}
      </text>
    </svg>
  );
}
