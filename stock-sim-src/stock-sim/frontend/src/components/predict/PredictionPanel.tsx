import { pct, pct0, won } from "@/lib/format";
import RankLadder, { TOP_N } from "@/components/predict/RankLadder";
import type { ModelSkill, Prediction } from "@/types";

function arc(f: number, cx = 80, cy = 78, r = 58) {
  const t = Math.PI * (1 - f);
  return `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${(cx + r * Math.cos(t)).toFixed(2)} ${(cy - r * Math.sin(t)).toFixed(2)}`;
}

/** 예측력 낮음 안내 (오프라인 검증에서 기준선을 확실히 넘지 못한 모델) */
export function SkillNotice({ skill }: { skill?: ModelSkill | null }) {
  if (!skill || skill.level === "ok") return null;
  const stats = `순위상관 IC ${skill.ic.toFixed(3)}, IC 양수 구간 ${skill.ic_positive_folds}/${skill.folds}, Brier skill ${
    skill.brier_skill >= 0 ? "+" : ""
  }${skill.brier_skill.toFixed(3)}`;
  if (skill.level === "ranking") {
    return (
      <div className="banner" role="note">
        <b>예측력 안내.</b> 과거 검증(walk-forward)에서 이 모델은 종목 간 상대 순위를 약하게 맞혔지만, 상승확률의 절대
        수준은 &lsquo;과거 상승 비율&rsquo;과 비슷했습니다 ({stats}). 종목끼리 비교하는 용도로 참고하세요.
      </div>
    );
  }
  return (
    <div className="banner" role="note">
      <b>예측력 낮음.</b> 과거 검증(walk-forward)에서 이 모델은 &lsquo;과거 상승 비율&rsquo;을 그대로 쓰는 기준선보다
      뚜렷하게 낫지 않았습니다 ({stats}). 확률과 범위는 참고용으로만 보세요.
    </div>
  );
}

/** 예측 카드 (SPEC 13.2-3). 이 모델의 예측력은 종목 간 순위에 있어(IC) 순위를 크게, 보정된 확률은 작게 보여준다.
 * 순위가 없으면(예전 예측) 상승확률 게이지로 대신한다. */
export function ProbabilityGauge({
  p_up,
  p_outperform,
  rank,
}: Pick<Prediction, "p_up" | "p_outperform"> & { rank?: Prediction["rank"] }) {
  const outperform = p_outperform == null ? "코스피 초과 확률 없음" : `코스피를 앞설 확률 ${pct0(p_outperform)}`;
  if (rank?.position != null && rank.of != null) {
    const top = rank.position <= TOP_N;
    return (
      <div className="panel rank-card">
        <div className="panel-h">
          <span>5거래일 뒤 상대 순위</span>
        </div>
        <div className="rank-hero">
          <span className={`rank-hero-num${top ? " is-top" : ""}`}>{rank.position}</span>
          <span className="rank-hero-of">위 / {rank.of}종목</span>
        </div>
        <RankLadder rank={rank.position} of={rank.of} large />
        <p className="rank-card-note">
          {top ? `상위 ${TOP_N}위 안이라 AI 계정이 다음 거래일에 삽니다. ` : ""}
          상승확률 {pct0(p_up)}, {outperform}.
        </p>
      </div>
    );
  }
  return (
    <div className="panel">
      <div className="panel-h">
        <span>5거래일 상승확률</span>
      </div>
      <svg viewBox="0 0 160 96" width="100%" style={{ maxHeight: 140 }} role="img">
        <title>{`상승확률 ${pct0(p_up)}`}</title>
        <path d={arc(0.999)} fill="none" stroke="var(--surface-2)" strokeWidth="12" strokeLinecap="round" />
        <path d={arc(Math.max(p_up, 0.001))} fill="none" stroke="var(--up)" strokeWidth="12" strokeLinecap="round" />
        <text x="80" y="74" textAnchor="middle" fontSize="26" fontWeight="600" fill="var(--text)">
          {pct0(p_up)}
        </text>
        <text x="80" y="90" textAnchor="middle" fontSize="10" fill="var(--text-2)">
          {outperform}
        </text>
      </svg>
    </div>
  );
}

/** 예상 가격 범위 하단/중앙/상단 (FR-PR-4: 호가 단위 반올림된 값) */
export function RangeCard({ close, range }: Pick<Prediction, "close" | "range">) {
  const lo = Math.min(range.price_low, close) * 0.99;
  const hi = Math.max(range.price_high, close) * 1.01;
  const x = (v: number) => 10 + ((v - lo) / (hi - lo)) * 280;
  return (
    <div className="panel">
      <div className="panel-h">
        <span>예상 가격 범위 (5거래일 뒤)</span>
        <span>하단, 중앙, 상단</span>
      </div>
      <svg viewBox="0 0 300 100" width="100%" style={{ maxHeight: 150 }} role="img">
        <title>{`예상 범위 ${won(range.price_low)}원에서 ${won(range.price_high)}원`}</title>
        <rect x="10" y="42" width="280" height="8" rx="2" fill="var(--surface-2)" />
        <rect
          x={x(range.price_low)}
          y="42"
          width={Math.max(x(range.price_high) - x(range.price_low), 1)}
          height="8"
          rx="2"
          fill="var(--up)"
          opacity=".28"
        />
        <line x1={x(close)} x2={x(close)} y1="34" y2="58" stroke="var(--text-2)" strokeDasharray="2 2" />
        <rect x={x(range.price_mid) - 1.5} y="38" width="3" height="16" fill="var(--up)" />
        <text x={x(range.price_low)} y="74" textAnchor="middle" fontSize="11" fill="var(--dn)">
          {won(range.price_low)}
        </text>
        <text x={x(range.price_low)} y="88" textAnchor="middle" fontSize="10" fill="var(--text-2)">
          {pct(range.q10)}
        </text>
        <text x={x(range.price_mid)} y="30" textAnchor="middle" fontSize="11" fill="var(--text)">
          {won(range.price_mid)} ({pct(range.q50)})
        </text>
        <text x={x(range.price_high)} y="74" textAnchor="middle" fontSize="11" fill="var(--up)">
          {won(range.price_high)}
        </text>
        <text x={x(range.price_high)} y="88" textAnchor="middle" fontSize="10" fill="var(--text-2)">
          {pct(range.q90)}
        </text>
        <text x={x(close)} y="88" textAnchor="middle" fontSize="10" fill="var(--text-2)">
          기준 {won(close)}
        </text>
      </svg>
    </div>
  );
}
