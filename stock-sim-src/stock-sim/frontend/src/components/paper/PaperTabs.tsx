"use client";

import { useState } from "react";
import useSWR from "swr";

import { Empty, ErrorBox, Skeleton } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiRequest, swrFetcher } from "@/lib/api";
import { REJECT_LABEL, STATUS_LABEL, kstDateTime, pctValue, tone, won } from "@/lib/format";
import type { Holding, Order, Trade } from "@/types";

type Tab = "holdings" | "open" | "orders" | "trades";
const STATUS_FILTERS = ["", "FILLED", "PARTIAL", "PENDING", "CANCELED", "EXPIRED", "REJECTED"];

/** 하단 탭: 보유종목 / 미체결 / 주문내역 / 체결내역 (SPEC 13.3) */
export default function PaperTabs({
  token,
  holdings,
  names,
  version,
  onChanged,
}: {
  token: string;
  holdings: Holding[];
  names: Record<string, string>;
  version: number; // 체결/주문 이벤트가 오면 올라가 목록을 다시 읽는다
  onChanged: () => void;
}) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("holdings");
  const [status, setStatus] = useState("");
  const open = useSWR<Order[]>([`/paper/orders?status=PENDING,PARTIAL&limit=100&v=${version}`, token], swrFetcher);
  const orders = useSWR<Order[]>(
    [`/paper/orders?limit=100${status ? `&status=${status}` : ""}&v=${version}`, token],
    swrFetcher,
  );
  const trades = useSWR<Trade[]>(tab === "trades" ? [`/paper/trades?limit=100&v=${version}`, token] : null, swrFetcher);
  const nm = (c: string) => names[c] ?? c;

  const cancel = async (o: Order) => {
    try {
      await apiRequest(`/paper/orders/${o.id}`, { method: "DELETE", token });
      toast(`취소 · ${nm(o.code)} ${o.qty - o.filled_qty}주`);
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "취소하지 못했습니다", "err");
    }
    onChanged();
  };

  const orderRows = (list: Order[], withCancel: boolean) => (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>종목</th>
            <th>구분</th>
            <th>가격</th>
            <th>체결/주문</th>
            <th>상태</th>
            <th>시각</th>
            {withCancel && <th />}
          </tr>
        </thead>
        <tbody>
          {list.map((o) => (
            <tr key={o.id}>
              <td>{nm(o.code)}</td>
              <td className={o.side === "BUY" ? "up" : "dn"}>
                {o.side === "BUY" ? "매수" : "매도"} {o.order_type === "MARKET" ? "시장가" : "지정가"}
                {o.source === "AI" ? " (AI)" : ""}
              </td>
              <td>{o.limit_price ? won(o.limit_price) : "-"}</td>
              <td>
                {won(o.filled_qty)}/{won(o.qty)}
              </td>
              <td title={o.reject_reason ?? undefined}>
                {STATUS_LABEL[o.status]}
                {o.queued ? " · 다음 장" : ""}
                {o.reject_reason ? ` · ${REJECT_LABEL[o.reject_reason] ?? o.reject_reason}` : ""}
              </td>
              <td className="label">{kstDateTime(o.created_at)}</td>
              {withCancel && (
                <td>
                  <button className="btn btn-sm" onClick={() => cancel(o)}>
                    취소
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  const body = () => {
    if (tab === "holdings") {
      if (!holdings.length) return <Empty title="보유 종목이 없습니다" />;
      return (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>종목</th>
                <th>수량</th>
                <th>평단</th>
                <th>현재가</th>
                <th>평가금액</th>
                <th>평가손익</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((h) => (
                <tr key={h.code}>
                  <td>{nm(h.code)}</td>
                  <td>
                    {won(h.qty)}
                    {h.reserved_qty ? <span className="label"> (매도대기 {h.reserved_qty})</span> : null}
                  </td>
                  <td>{won(Math.round(h.avg_price))}</td>
                  <td>{won(h.price)}</td>
                  <td>{won(h.value)}</td>
                  <td className={tone(h.pnl)}>
                    {h.pnl > 0 ? "+" : ""}
                    {won(h.pnl)} ({pctValue(h.return_pct)})
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    const src = tab === "open" ? open : tab === "orders" ? orders : trades;
    if (src.error) return <ErrorBox message="내역을 불러오지 못했습니다." onRetry={() => src.mutate()} />;
    if (!src.data) return <Skeleton lines={4} />;
    if (tab === "open")
      return open.data!.length ? orderRows(open.data!, true) : <Empty title="미체결 주문이 없습니다" />;
    if (tab === "orders")
      return (
        <>
          <select
            className="input"
            style={{ width: 160, marginBottom: 8 }}
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="상태 필터"
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s ? STATUS_LABEL[s] : "전체 상태"}
              </option>
            ))}
          </select>
          {orders.data!.length ? orderRows(orders.data!, false) : <Empty title="주문 내역이 없습니다" />}
        </>
      );
    const ts = trades.data!;
    if (!ts.length) return <Empty title="체결 내역이 없습니다" />;
    return (
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>종목</th>
              <th>구분</th>
              <th>체결가</th>
              <th>수량</th>
              <th>금액</th>
              <th>수수료·세금</th>
              <th>시각</th>
            </tr>
          </thead>
          <tbody>
            {ts.map((t) => (
              <tr key={t.id}>
                <td>{nm(t.code)}</td>
                <td className={t.side === "BUY" ? "up" : "dn"}>{t.side === "BUY" ? "매수" : "매도"}</td>
                <td>{won(t.price)}</td>
                <td>{won(t.qty)}</td>
                <td>{won(t.amount)}</td>
                <td>
                  {won(t.fee)}
                  {t.tax ? ` · ${won(t.tax)}` : ""}
                </td>
                <td className="label">{kstDateTime(t.filled_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  const tabs: [Tab, string][] = [
    ["holdings", `보유종목 ${holdings.length}`],
    ["open", `미체결 ${open.data?.length ?? ""}`],
    ["orders", "주문내역"],
    ["trades", "체결내역"],
  ];
  return (
    <div className="panel">
      <div className="tabs" role="tablist">
        {tabs.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>
      {body()}
    </div>
  );
}
