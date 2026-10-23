import { Empty } from "@/components/ui/States";
import { pct, pct0 } from "@/lib/format";
import type { HistoryItem, HistorySummary } from "@/types";

/** 이 종목 예측 기록: 최근 20건의 예측 범위 vs 실제 결과, 적중률 (SPEC 13.2-7). 나쁜 결과도 그대로 보인다. */
export default function TrackRecord({ items, summary }: { items: HistoryItem[]; summary?: HistorySummary }) {
  const rows = items.slice(0, 20);
  const vals = rows.flatMap((r) => [r.range.q10, r.range.q90, r.outcome?.actual_return ?? 0]);
  const lo = Math.min(...vals, -0.01);
  const hi = Math.max(...vals, 0.01);
  const x = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`;
  return (
    <div className="panel">
      <div className="panel-h">
        <span>이 종목 예측 기록 (최근 {rows.length}건)</span>
        <span>
          {summary?.n
            ? `방향 적중 ${pct0(summary.hit_rate ?? 0)} · 범위 적중 ${pct0(summary.range_coverage ?? 0)} (결과 ${summary.n}건)`
            : "결과 대기"}
        </span>
      </div>
      {rows.length === 0 ? (
        <Empty title="아직 예측 기록이 없습니다">예측 후 5거래일이 지나면 실제 결과가 채워집니다.</Empty>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {rows.map((r) => (
            <div key={r.as_of_date} style={{ display: "grid", gridTemplateColumns: "84px 1fr 64px", gap: 8, alignItems: "center" }}>
              <span className="label">{r.as_of_date.slice(5)}</span>
              <div className="axis" style={{ height: 10 }} title={`범위 ${pct(r.range.q10)} ~ ${pct(r.range.q90)}`}>
                <b style={{ left: x(0) }} />
                <i style={{ left: x(r.range.q10), width: `calc(${x(r.range.q90)} - ${x(r.range.q10)})`, background: "var(--surface-2)", border: "0.5px solid var(--border-strong)" }} />
                {r.outcome && (
                  <i
                    style={{
                      left: `calc(${x(r.outcome.actual_return)} - 4px)`,
                      width: 8,
                      height: 8,
                      top: 1,
                      borderRadius: 4,
                      background: r.outcome.actual_return >= 0 ? "var(--up)" : "var(--dn)",
                    }}
                  />
                )}
              </div>
              <span className="label" style={{ textAlign: "right" }}>
                {r.outcome ? `${r.outcome.hit_direction ? "적중" : "빗나감"} ${pct(r.outcome.actual_return)}` : "대기"}
              </span>
            </div>
          ))}
          <div className="label">회색 막대 = 예측 범위(q10~q90), 점 = 실제 5거래일 수익률</div>
        </div>
      )}
    </div>
  );
}
