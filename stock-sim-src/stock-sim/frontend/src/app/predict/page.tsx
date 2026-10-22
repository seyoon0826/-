import SourceChip from "@/components/SourceChip";
import StockTable from "@/components/predict/StockTable";
import { apiGet, withFallback } from "@/lib/api";
import { stockList } from "@/lib/mock";
import type { StockSummary } from "@/types";

export default async function PredictList() {
  const { data, live } = await withFallback(() => apiGet<StockSummary[]>("/stocks"), stockList);
  return (
    <>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <SourceChip live={live} label={live ? "시세 실데이터" : "시세 예시 데이터"} />
        <span className="label">상승확률은 예측 모델(Phase 5~6)이 생기면 채워집니다.</span>
      </div>
      <StockTable stocks={data} />
    </>
  );
}
