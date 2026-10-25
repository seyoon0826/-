"use client";

import useSWR from "swr";

import { Empty, ErrorBox, Skeleton } from "@/components/ui/States";
import { swrFetcher } from "@/lib/api";
import { kstDateTime, pct0, won } from "@/lib/format";
import type { Order } from "@/types";

const STATUS: Record<string, string> = {
  FILLED: "체결",
  PARTIAL: "부분체결",
  PENDING: "대기",
  CANCELED: "취소",
  EXPIRED: "만료",
  REJECTED: "거부",
};

/** AI 계좌 최근 주문과 근거 예측 (FR-AI-2/4). 규칙(AI_MODE=rank): 상승확률 순위 상위 5종목 매수, 10위 밖이면 매도. */
export default function AiOrders({
  token,
  names,
  version,
}: {
  token: string;
  names: Record<string, string>;
  version: number;
}) {
  const q = useSWR<Order[]>([`/paper/orders?account=ai&limit=10&v=${version}`, token], swrFetcher);
  return (
    <section className="panel">
      <div className="panel-h">
        <span>AI 최근 매매</span>
        <span className="label">매일 09:05에 순위 상위 5종목을 사고, 10위 밖으로 밀리면 팝니다</span>
      </div>
      {q.error ? (
        <ErrorBox message="AI 주문을 불러오지 못했습니다." onRetry={() => q.mutate()} />
      ) : !q.data ? (
        <Skeleton lines={3} />
      ) : q.data.length === 0 ? (
        <Empty title="아직 AI 주문이 없습니다">
          예측이 생기면 다음 거래일 09:05에 순위 상위 5종목을 같은 비중으로 삽니다.
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>시각</th>
                <th>종목</th>
                <th>구분</th>
                <th className="num">수량</th>
                <th>상태</th>
                <th>근거</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((o) => (
                <tr key={o.id}>
                  <td>{o.created_at ? kstDateTime(o.created_at) : "-"}</td>
                  <td>{names[o.code] ?? o.code}</td>
                  <td className={o.side === "BUY" ? "up" : "dn"}>{o.side === "BUY" ? "매수" : "매도"}</td>
                  <td className="num">{won(o.qty)}</td>
                  <td>{STATUS[o.status] ?? o.status}</td>
                  <td className="label">
                    {o.basis ? `${o.basis.as_of_date} 예측 상승확률 ${pct0(o.basis.p_up)}` : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
