"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";

import ConnectionBadge, { DisconnectBanner } from "@/components/ui/ConnectionBadge";
import { Empty, ErrorBox, Skeleton } from "@/components/ui/States";
import { swrFetcher } from "@/lib/api";
import { arrow, pct0, pctValue, tone, won } from "@/lib/format";
import { useQuotes } from "@/lib/ws";
import type { StockSummary } from "@/types";

type SortKey = "name" | "price" | "change_pct" | "p_up";

/** 종목 목록 (SPEC 13.1): 현재가(실시간), 등락률, 상승확률 막대, 예측 기준일. 정렬·검색. */
export default function StockTable() {
  const { data, error, isLoading, mutate } = useSWR<StockSummary[]>(["/stocks", null], swrFetcher, {
    refreshInterval: 60_000,
  });
  const codes = useMemo(() => (data ?? []).map((s) => s.code), [data]);
  const { quotes, status, conn } = useQuotes(codes);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "p_up", dir: -1 });

  const rows = useMemo(() => {
    const merged = (data ?? []).map((s) => {
      const live = quotes[s.code];
      return live ? { ...s, price: live.price, change_pct: live.change_pct } : s;
    });
    const term = q.trim();
    return merged
      .filter((s) => !term || s.name.includes(term) || s.code.includes(term))
      .sort((a, b) => {
        const av = a[sort.key] ?? -Infinity;
        const bv = b[sort.key] ?? -Infinity;
        return (av > bv ? 1 : av < bv ? -1 : 0) * sort.dir;
      });
  }, [data, quotes, q, sort]);

  const asOf = data?.find((s) => s.as_of_date)?.as_of_date;
  const head = (key: SortKey, label: string) => (
    <th
      aria-sort={sort.key === key ? (sort.dir === -1 ? "descending" : "ascending") : "none"}
      onClick={() => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : -1 }))}
    >
      {label}
      {sort.key === key ? (sort.dir === -1 ? " ↓" : " ↑") : ""}
    </th>
  );

  return (
    <>
      <DisconnectBanner conn={conn} />
      <div className="panel">
        <div className="panel-h" style={{ flexWrap: "wrap" }}>
          <span>코스피 시총 상위 · 5거래일 예측 {asOf ? `(기준일 ${asOf})` : ""}</span>
          <span style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <ConnectionBadge conn={conn} status={status} />
            <input
              className="input"
              style={{ width: 160 }}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="삼성전자 또는 005930"
              aria-label="종목 검색"
            />
          </span>
        </div>
        {isLoading ? (
          <Skeleton lines={8} />
        ) : error ? (
          <ErrorBox message="종목 목록을 불러오지 못했습니다." onRetry={() => mutate()} />
        ) : !data?.length ? (
          <Empty title="종목이 아직 없습니다">수집 작업(make backfill)이 끝나면 나타납니다.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {head("name", "종목")}
                  {head("price", "현재가")}
                  {head("change_pct", "등락률")}
                  {head("p_up", "상승확률")}
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.code}>
                    <td>
                      <Link href={`/predict/${s.code}`} style={{ fontWeight: 500 }}>
                        {s.name}
                      </Link>{" "}
                      <span className="label">{s.code}</span>
                    </td>
                    <td>{s.price == null ? "-" : won(s.price)}</td>
                    <td className={tone(s.change_pct ?? 0)}>
                      {s.change_pct == null ? "-" : `${arrow(s.change_pct)} ${pctValue(s.change_pct)}`}
                    </td>
                    <td>
                      {s.p_up == null ? (
                        <span className="label">다음 장 마감 후 생성</span>
                      ) : (
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                          <span className="bar" style={{ width: 70 }} aria-hidden>
                            <i style={{ width: pct0(s.p_up), background: "var(--up)" }} />
                          </span>
                          <span style={{ minWidth: 34 }}>{pct0(s.p_up)}</span>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={4} className="label" style={{ textAlign: "center", padding: 24 }}>
                      검색 결과가 없습니다.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
