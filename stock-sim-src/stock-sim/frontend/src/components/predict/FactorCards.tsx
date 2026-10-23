import type { Factor } from "@/types";

/** 예측 근거 TOP5 (SHAP, SPEC 13.2-4). 막대 = 이 요인이 상승확률을 높였는지(오른쪽) 낮췄는지(왼쪽).
 * 요인 문장의 상승/하락(예: "KOSPI 20일 -14.6% 하락")과 기여 방향은 다를 수 있다. */
export default function FactorCards({ factors }: { factors: Factor[] }) {
  const max = Math.max(...factors.map((f) => Math.abs(f.shap)), 1e-9);
  return (
    <div className="panel">
      <div className="panel-h">
        <span>관련 요인 (SHAP 상위 5)</span>
        <span title="막대는 각 요인이 모델의 상승확률을 높였는지 낮췄는지를 뜻합니다">
          상승확률 <span className="dn">◀ 낮춤</span> | <span className="up">높임 ▶</span>
        </span>
      </div>
      {factors.map((f) => {
        const w = (Math.abs(f.shap) / max) * 50;
        const positive = f.direction === "+";
        return (
          <div className="factor-row" key={f.rank}>
            <span>
              {f.label}
              <span className="sr-only">{positive ? " (상승확률 높임)" : " (상승확률 낮춤)"}</span>
            </span>
            <div className="axis" title={`${positive ? "상승확률을 높임" : "상승확률을 낮춤"} (기여도 ${f.shap.toFixed(3)})`}>
              <b />
              <i
                style={{
                  left: positive ? "50%" : `${50 - w}%`,
                  width: `${w}%`,
                  background: positive ? "var(--up)" : "var(--dn)",
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
