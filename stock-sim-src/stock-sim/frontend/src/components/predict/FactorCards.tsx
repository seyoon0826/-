import type { Factor } from "@/types";

/** 예측 근거 TOP5 (SHAP). 가운데 기준 좌=하락 기여, 우=상승 기여. */
export default function FactorCards({ factors }: { factors: Factor[] }) {
  const max = Math.max(...factors.map((f) => Math.abs(f.shap)), 1e-9);
  return (
    <div className="panel">
      <div className="panel-h">
        <span>관련 요인 (SHAP 상위 5)</span>
        <span>
          <span className="dn">◀ 하락</span> | <span className="up">상승 ▶</span>
        </span>
      </div>
      {factors.map((f) => {
        const w = (Math.abs(f.shap) / max) * 50;
        const positive = f.direction === "+";
        return (
          <div className="factor-row" key={f.rank}>
            <span>
              <span className={positive ? "up" : "dn"} aria-hidden>
                {positive ? "▲" : "▼"}
              </span>{" "}
              {f.label}
            </span>
            <div className="axis" title={`기여도 ${f.shap.toFixed(3)}`}>
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
