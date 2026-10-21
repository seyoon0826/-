import StockTable from "@/components/predict/StockTable";
import { stockList } from "@/lib/mock";

export default function PredictList() {
  return <StockTable stocks={stockList()} />;
}
