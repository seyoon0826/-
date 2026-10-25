import RankLadder, { TOP_N } from "@/components/predict/RankLadder";
import { pct0, won } from "@/lib/format";
import type { Prediction } from "@/types";

/** 소수 수익률 -> "+1.2%" (부호를 글자에 넣는다) */
export function signedPct(v: number, digits = 1): string {
  const s = (v * 100).toFixed(digits);
  return `${v > 0 ? "+" : v < 0 ? "" : "±"}${s === "-0.0" ? "0.0" : s}%`;
}

/** 같은 순위대 종목이 과거에 오른 비율로 고른 방향 문장 (반반 기준 48~52%) */
export function direction(upRatio: number): { word: string; tone: "up" | "dn" | "" } {
  if (upRatio >= 0.52) return { word: "오를 가능성이 조금 더 큼", tone: "up" };
  if (upRatio <= 0.48) return { word: "내릴 가능성이 조금 더 큼", tone: "dn" };
  return { word: "오를지 내릴지 반반", tone: "" };
}

/** 예상 중앙값으로 고른 한 단어. 보합 기준 ±0.5%, 큰 움직임 기준 ±2% */
export function verdict(q50: number): { word: string; tone: "up" | "dn" | "" } {
  if (Math.abs(q50) < 0.005) return { word: "보합", tone: "" };
  if (q50 > 0) return { word: q50 >= 0.02 ? "상승 예상" : "소폭 상승 예상", tone: "up" };
  return { word: q50 <= -0.02 ? "하락 예상" : "소폭 하락 예상", tone: "dn" };
}

/** 0 을 가운데 둔 예상 범위 막대: 10~90% 구간과 중앙값 표시. scale 은 양쪽 끝의 절댓값. */
export function RangeBar({
  q10,
  q50,
  q90,
  scale,
  large = false,
}: {
  q10: number;
  q50: number;
  q90: number;
  scale: number;
  large?: boolean;
}) {
  const pos = (v: number) => Math.min(Math.max(50 + (v / scale) * 50, 0), 100);
  const lo = Math.min(q10, q90);
  const hi = Math.max(q10, q90);
  // 0 보다 아래(하락) 구간은 파랑, 위(상승) 구간은 빨강
  const seg = (a: number, b: number, cls: string) =>
    b > a ? <span className={cls} style={{ left: `${pos(a)}%`, width: `${pos(b) - pos(a)}%` }} /> : null;
  return (
    <span className={`rbar${large ? " rbar-lg" : ""}`} aria-hidden>
      {seg(lo, Math.min(hi, 0), "rbar-dn")}
      {seg(Math.max(lo, 0), hi, "rbar-up")}
      <span className="rbar-zero" />
      <span className={`rbar-mid ${q50 >= 0 ? "up" : "dn"}`} style={{ left: `${pos(q50)}%` }} />
    </span>
  );
}

/** 상세 화면의 첫 블록. 질문: "5거래일 뒤 오를까 내릴까, 얼마나 움직일까?"
 * 방향은 같은 순위대 종목이 과거에 실제로 오른 비율로, 크기는 종목별 예상 범위(10~90%)로 답한다.
 * (종목별 평균 수익률 예측은 검증에서 구분력이 없어 보여주지 않는다) */
export default function ForecastHero({ p, name }: { p: Prediction; name: string }) {
  const { q10, q50, q90, price_low, price_high } = p.range;
  const scale = Math.max(Math.abs(q10), Math.abs(q90), 0.03) * 1.1;
  const rank = p.rank?.position;
  const of = p.rank?.of;
  const e = p.expected;
  const dir = e ? direction(e.up_ratio) : null;
  return (
    <section className="panel forecast" aria-labelledby="forecast-h">
      <h2 id="forecast-h" className="sr-only">
        {name} 5거래일 예측
      </h2>
      <div className="forecast-main">
        <div className="forecast-range-head">
          <span>5거래일 뒤 오를까</span>
        </div>
        {e && dir ? (
          <>
            <div className="forecast-figure">
              <span className={`forecast-num ${dir.tone}`}>{pct0(e.up_ratio)}</span>
              <span className={`forecast-word ${dir.tone}`}>{dir.word}</span>
            </div>
            <p className="forecast-note">
              {rank}위처럼 {e.tier}이던 종목은 지난 검증 기간에 {won(e.n)}번 중 {won(e.ups)}번 5거래일 뒤 올랐습니다.
            </p>
          </>
        ) : (
          <>
            <div className="forecast-figure">
              <span className="forecast-num">{pct0(p.p_up)}</span>
              <span className="forecast-word">모델이 본 상승 확률</span>
            </div>
          </>
        )}
      </div>

      <div className="forecast-range">
        <div className="forecast-range-head">
          <span>얼마나 움직일 수 있나</span>
          <span className="label">10번 중 8번은 이 안에서 끝난다고 봅니다</span>
        </div>
        <RangeBar q10={q10} q50={q50} q90={q90} scale={scale} large />
        <div className="forecast-ends">
          <span>
            <b className="dn">{signedPct(q10)}</b>
            <span className="label">{won(price_low)}원까지 하락</span>
          </span>
          <span className="forecast-end-mid">
            <span className="label">가운데 {signedPct(q50)}</span>
          </span>
          <span className="forecast-end-right">
            <b className="up">{signedPct(q90)}</b>
            <span className="label">{won(price_high)}원까지 상승</span>
          </span>
        </div>
      </div>

      <div className="forecast-rank">
        <div className="forecast-range-head">
          <span>다른 종목과 비교하면</span>
        </div>
        {rank != null && of != null ? (
          <>
            <div className="rank-hero">
              <span className={`rank-hero-num${rank <= TOP_N ? " is-top" : ""}`}>{rank}</span>
              <span className="rank-hero-of">위, {of}종목 중</span>
            </div>
            <RankLadder rank={rank} of={of} large />
            <p className="forecast-note">
              {rank <= TOP_N ? "상위 5위 안이라 AI 계정이 다음 거래일에 삽니다." : "AI 계정은 상위 5위만 삽니다."}
            </p>
          </>
        ) : (
          <span className="label">-</span>
        )}
      </div>

      <p className="forecast-honest">
        이 모델은 방향을 크게 맞히지 못합니다. 위의 범위를 이 종목을 5거래일 들고 있을 때 감수할 위험의 크기로 보세요.{" "}
        {p.as_of_date} 종가 {won(p.close)}원 기준입니다.
      </p>
    </section>
  );
}
