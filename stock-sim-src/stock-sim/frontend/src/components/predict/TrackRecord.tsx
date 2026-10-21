import { pct0 } from "@/lib/format";
import type { TrackRecordSummary } from "@/types";

/** 이 종목의 과거 예측 적중 기록. 베이스라인보다 나쁜 지표도 그대로 보여준다(SPEC FR-PF-6). */
export default function TrackRecord({ data }: { data: TrackRecordSummary }) {
  const hitCount = data.hits.filter(Boolean).length;
  return (
    <div className="panel">
      <div className="panel-h">
        <span>이 종목 예측 기록 (최근 {data.n}건)</span>
        <span>
          적중 {hitCount}/{data.hits.length}
        </span>
      </div>
      <div className="record">
        {data.hits.map((h, i) => (
          <span key={i} className={h ? "hit" : ""} title={h ? "방향 적중" : "빗나감"} />
        ))}
      </div>
      <div className="grid-2" style={{ marginTop: 12, gap: 8 }}>
        <div>
          <div className="label">모델 방향 정확도</div>
          <div className="value">{pct0(data.direction_accuracy)}</div>
        </div>
        <div>
          <div className="label">항상 상승 기준</div>
          <div className="value" style={{ color: "var(--text-2)" }}>
            {pct0(data.baseline_accuracy)}
          </div>
        </div>
        <div>
          <div className="label">범위 적중률 (목표 80%)</div>
          <div className="value">{pct0(data.range_coverage)}</div>
        </div>
        <div>
          <div className="label">표본 수</div>
          <div className="value">{data.n}</div>
        </div>
      </div>
    </div>
  );
}
