export const TOP_N = 5; // AI 계정이 사는 상위 순위 (AI_TOP_N)

/** 같은 날 종목 사이에서 이 종목의 자리를 띠 위의 점으로 보여준다 (왼쪽 = 1위, 음영 = 상위 TOP_N). */
export default function RankLadder({ rank, of, large = false }: { rank: number; of: number; large?: boolean }) {
  const x = of > 1 ? ((rank - 1) / (of - 1)) * 100 : 0;
  return (
    <span className={`ladder${large ? " ladder-lg" : ""}`} aria-hidden>
      <span className="ladder-top" style={{ width: `${(TOP_N / of) * 100}%` }} />
      <span className={`ladder-dot${rank <= TOP_N ? " is-top" : ""}`} style={{ left: `${x}%` }} />
    </span>
  );
}
