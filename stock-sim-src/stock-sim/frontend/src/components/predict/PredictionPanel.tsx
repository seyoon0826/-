import { pct, pct0, won } from "@/lib/format";
import type { Prediction } from "@/types";

function arc(f: number, cx = 80, cy = 78, r = 58) {
  const t = Math.PI * (1 - f);
  return `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${(cx + r * Math.cos(t)).toFixed(2)} ${(cy - r * Math.sin(t)).toFixed(2)}`;
}

export function ProbabilityGauge({ p_up, p_outperform }: Pick<Prediction, "p_up" | "p_outperform">) {
  return (
    <div className="panel">
      <div className="panel-h">
        <span>5거래일 상승확률</span>
        <span className="chip">보정됨</span>
      </div>
      <svg viewBox="0 0 160 96" width="100%" style={{ maxHeight: 140 }} role="img">
        <title>{`상승확률 ${pct0(p_up)}`}</title>
        <path d={arc(0.999)} fill="none" stroke="var(--surface-2)" strokeWidth="12" strokeLinecap="round" />
        <path d={arc(p_up)} fill="none" stroke="var(--up)" strokeWidth="12" strokeLinecap="round" />
        <text x="80" y="74" textAnchor="middle" fontSize="26" fontWeight="600" fill="var(--text)">
          {pct0(p_up)}
        </text>
        <text x="80" y="90" textAnchor="middle" fontSize="10" fill="var(--text-2)">
          KOSPI 초과 {pct0(p_outperform)}
        </text>
      </svg>
    </div>
  );
}

export function RangeCard({ close, range }: Pick<Prediction, "close" | "range">) {
  // 현재가를 포함하도록 축을 잡고, 범위(q10~q90)를 막대로 표시
  const lo = Math.min(range.price_low, close) * 0.99;
  const hi = Math.max(range.price_high, close) * 1.01;
  const x = (v: number) => 10 + ((v - lo) / (hi - lo)) * 280;
  return (
    <div className="panel">
      <div className="panel-h">
        <span>예상 가격 범위</span>
        <span>q10 · q50 · q90</span>
      </div>
      <svg viewBox="0 0 300 100" width="100%" style={{ maxHeight: 150 }} role="img">
        <title>{`예상 범위 ${won(range.price_low)}원에서 ${won(range.price_high)}원`}</title>
        <rect x="10" y="42" width="280" height="8" rx="2" fill="var(--surface-2)" />
        <rect
          x={x(range.price_low)}
          y="42"
          width={x(range.price_high) - x(range.price_low)}
          height="8"
          rx="2"
          fill="var(--up)"
          opacity=".28"
        />
        <line
          x1={x(close)}
          x2={x(close)}
          y1="34"
          y2="58"
          stroke="var(--text-2)"
          strokeDasharray="2 2"
        />
        <rect x={x(range.price_mid) - 1.5} y="38" width="3" height="16" fill="var(--up)" />
        <text x={x(range.price_low)} y="74" textAnchor="middle" fontSize="11" fill="var(--dn)">
          {won(range.price_low)}
        </text>
        <text x={x(range.price_low)} y="88" textAnchor="middle" fontSize="10" fill="var(--text-2)">
          {pct(range.q10)}
        </text>
        <text x={x(range.price_mid)} y="30" textAnchor="middle" fontSize="11" fill="var(--text)">
          {won(range.price_mid)}
        </text>
        <text x={x(range.price_high)} y="74" textAnchor="middle" fontSize="11" fill="var(--up)">
          {won(range.price_high)}
        </text>
        <text x={x(range.price_high)} y="88" textAnchor="middle" fontSize="10" fill="var(--text-2)">
          {pct(range.q90)}
        </text>
        <text x={x(close)} y="88" textAnchor="middle" fontSize="10" fill="var(--text-2)">
          현재 {won(close)}
        </text>
      </svg>
    </div>
  );
}
