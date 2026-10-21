"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { arrow, pct, pct0, tone, won } from "@/lib/format";
import type { StockSummary } from "@/types";

type SortKey = "name" | "price" | "change_pct" | "p_up";

export default function StockTable({ stocks }: { stocks: StockSummary[] }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "p_up", dir: -1 });

  const rows = useMemo(() => {
    const f = stocks.filter((s) => s.name.includes(q.trim()) || s.code.includes(q.trim()));
    return f.sort((a, b) => {
      const av = a[sort.key] ?? -1;
      const bv = b[sort.key] ?? -1;
      return (av > bv ? 1 : av < bv ? -1 : 0) * sort.dir;
    });
  }, [stocks, q, sort]);

  const head = (key: SortKey, label: string) => (
    <th onClick={() => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : -1 }))}>
      {label}
      {sort.key === key ? (sort.dir === -1 ? " ↓" : " ↑") : ""}
    </th>
  );

  return (
    <div className="panel">
      <div className="panel-h">
        <span>코스피 시총 상위 · 5거래일 예측 (기준일 {stocks[0]?.as_of_date ?? "-"})</span>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="종목명 또는 코드"
          aria-label="종목 검색"
          style={{
            padding: "6px 10px",
            borderRadius: 8,
            border: "0.5px solid var(--border-strong)",
            background: "var(--surface)",
            color: "var(--text)",
            fontSize: 13,
          }}
        />
      </div>
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
              <td>{won(s.price)}</td>
              <td className={tone(s.change_pct)}>
                {arrow(s.change_pct)} {pct(s.change_pct / 100)}
              </td>
              <td>
                {s.p_up == null ? (
                  <span className="label">예측 없음</span>
                ) : (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8, justifyContent: "flex-end" }}>
                    <span className="bar" style={{ width: 70 }}>
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
  );
}
