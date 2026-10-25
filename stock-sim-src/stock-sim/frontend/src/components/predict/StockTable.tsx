"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";

import { direction, RangeBar, signedPct } from "@/components/predict/Forecast";
import { TOP_N } from "@/components/predict/RankLadder";
import ConnectionBadge, { DisconnectBanner } from "@/components/ui/ConnectionBadge";
import { Empty, ErrorBox, Skeleton } from "@/components/ui/States";
import { swrFetcher } from "@/lib/api";
import { pctValue, tone, won } from "@/lib/format";
import { useQuotes } from "@/lib/ws";
import type { StockSummary } from "@/types";

type SortKey = "name" | "price" | "change_pct" | "rank";

const shortDate = (iso: string) => {
  const [, m, d] = iso.split("-").map(Number);
  return `${m}월 ${d}일`;
};

/** 예측 순위 (SPEC 13.1). 이 화면의 질문: "5거래일 뒤 어느 종목이 상대적으로 나을까, 얼마나 움직일까?" */
export default function StockTable() {
  const { data, error, isLoading, mutate } = useSWR<StockSummary[]>(["/stocks", null], swrFetcher, {
    refreshInterval: 60_000,
  });
  const codes = useMemo(() => (data ?? []).map((s) => s.code), [data]);
  const { quotes, status, conn } = useQuotes(codes);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "rank", dir: 1 });

  const rows = useMemo(() => {
    const merged = (data ?? []).map((s) => {
      const live = quotes[s.code];
      return live ? { ...s, price: live.price, change_pct: live.change_pct } : s;
    });
    const term = q.trim();
    return merged
      .filter((s) => !term || s.name.includes(term) || s.code.includes(term))
      .sort((a, b) => {
        const missing = sort.key === "rank" ? Infinity * sort.dir : -Infinity; // 값 없는 종목은 항상 뒤로
        const av = a[sort.key] ?? missing;
        const bv = b[sort.key] ?? missing;
        return (av > bv ? 1 : av < bv ? -1 : 0) * sort.dir;
      });
  }, [data, quotes, q, sort]);

  // 모든 행의 범위 막대가 같은 눈금을 쓰도록 (종목끼리 폭을 비교할 수 있게)
  const scale = useMemo(() => {
    const ends = (data ?? []).flatMap((s) => [Math.abs(s.q10 ?? 0), Math.abs(s.q90 ?? 0)]);
    return Math.max(0.03, ...ends) * 1.05;
  }, [data]);
  const asOf = data?.find((s) => s.as_of_date)?.as_of_date;
  const top = (data ?? []).filter((s) => s.rank != null && s.rank <= TOP_N).sort((a, b) => a.rank! - b.rank!);

  const head = (key: SortKey, label: string, title?: string) => (
    <th
      title={title}
      aria-sort={sort.key === key ? (sort.dir === -1 ? "descending" : "ascending") : "none"}
      onClick={() =>
        // 순위는 1위가 먼저 보이도록 처음 누르면 오름차순
        setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : key === "rank" ? 1 : -1 }))
      }
    >
      {label}
      {sort.key === key ? (sort.dir === -1 ? " ↓" : " ↑") : ""}
    </th>
  );

  return (
    <>
      <DisconnectBanner conn={conn} />
      <header className="screen-head">
        <div>
          <h1>예측 순위</h1>
          <p>
            {asOf
              ? `${shortDate(asOf)} 종가 기준, 코스피 대형주 30종목을 5거래일 뒤 상대적으로 나을 순서로 줄 세웠습니다.`
              : "코스피 시가총액 상위 30종목의 5거래일 예측입니다."}
          </p>
        </div>
        <div className="head-actions">
          <ConnectionBadge conn={conn} status={status} />
          <input
            className="input"
            style={{ width: 180 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="종목명 또는 코드"
            aria-label="종목 검색"
          />
        </div>
      </header>

      {top.length > 0 && (
        <p className="finding">
          상위 {TOP_N}종목은 {top.map((s) => s.name).join(", ")}입니다. AI 계정은 다음 거래일 09:05에 이 종목들을 같은
          금액씩 삽니다.
        </p>
      )}

      <section className="panel" aria-label="예측 순위 표">
        {isLoading ? (
          <Skeleton lines={8} />
        ) : error ? (
          <ErrorBox message="종목 목록을 불러오지 못했습니다." onRetry={() => mutate()} />
        ) : !data?.length ? (
          <Empty title="종목이 아직 없습니다">수집 작업(make backfill)이 끝나면 나타납니다.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table stock-table">
              <thead>
                <tr>
                  {head("rank", "순위")}
                  {head("name", "종목")}
                  {head("price", "현재가")}
                  {head("change_pct", "오늘")}
                  <th title="같은 순위대 종목이 지난 검증 기간에 5거래일 뒤 오른 비율">오를 확률</th>
                  <th className="range-col" title="10번 중 8번은 이 범위 안에서 끝난다고 봅니다. 가운데 선이 0%">
                    5거래일 뒤 범위
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => {
                  const d = s.expected ? direction(s.expected.up_ratio) : null;
                  return (
                    <tr key={s.code} className={s.rank != null && s.rank <= TOP_N ? "is-top" : undefined}>
                      <td className="rank-cell">
                        {s.rank == null ? <span className="label">-</span> : <span className="rank-num">{s.rank}</span>}
                      </td>
                      <td>
                        <Link href={`/predict/${s.code}`} className="stock-name">
                          {s.name}
                        </Link>{" "}
                        <span className="label">{s.code}</span>
                      </td>
                      <td>{s.price == null ? "-" : won(s.price)}</td>
                      <td className={tone(s.change_pct ?? 0)}>{s.change_pct == null ? "-" : pctValue(s.change_pct)}</td>
                      <td>
                        {!s.expected || !d ? (
                          <span className="label">{s.rank == null ? "장 마감 후 생성" : "-"}</span>
                        ) : (
                          <span
                            className="expect"
                            title={`${s.expected.tier}: ${s.expected.n}번 중 ${s.expected.ups}번 상승`}
                          >
                            <b className={d.tone}>{Math.round(s.expected.up_ratio * 100)}%</b>
                          </span>
                        )}
                      </td>
                      <td className="range-col">
                        {s.q10 != null && s.q50 != null && s.q90 != null ? (
                          <span
                            className="range-cell"
                            title={`10번 중 8번은 ${signedPct(s.q10)} ~ ${signedPct(s.q90)} 사이`}
                          >
                            <span className="dn">{signedPct(s.q10, 0)}</span>
                            <RangeBar q10={s.q10} q50={s.q50} q90={s.q90} scale={scale} />
                            <span className="up">{signedPct(s.q90, 0)}</span>
                          </span>
                        ) : (
                          <span className="label">-</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={6} className="label" style={{ textAlign: "center", padding: 24 }}>
                      &lsquo;{q}&rsquo;에 맞는 종목이 없습니다. 종목명이나 6자리 코드로 찾아보세요.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        {!!data?.length && (
          <p className="table-note">
            오를 확률은 같은 순위대(상위 30%, 중간 40%, 하위 30%) 종목이 지난 검증 기간에 5거래일 뒤 실제로 오른
            비율입니다. 막대는 10번 중 8번이 끝날 것으로 보는 범위로, 종목마다 감수할 위험의 크기가 다릅니다(가운데 선이
            0%). 종목명을 누르면 근거를 볼 수 있습니다.
          </p>
        )}
      </section>
    </>
  );
}
