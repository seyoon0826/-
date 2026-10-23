"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import useSWR from "swr";

import AccountSetup from "@/components/paper/AccountSetup";
import AiOrders from "@/components/paper/AiOrders";
import AuthGate from "@/components/paper/AuthGate";
import OrderPanel from "@/components/paper/OrderPanel";
import Orderbook from "@/components/paper/Orderbook";
import PaperTabs from "@/components/paper/PaperTabs";
import PerformanceChart from "@/components/paper/PerformanceChart";
import ConnectionBadge, { DisconnectBanner } from "@/components/ui/ConnectionBadge";
import { Empty, ErrorBox, Skeleton } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiRequest, swrFetcher } from "@/lib/api";
import { onUnauthorized, useToken } from "@/lib/auth";
import { arrow, krw, pctValue, tone, won } from "@/lib/format";
import { usePaperSocket, useQuotes } from "@/lib/ws";
import type { Accounts, OrderbookResponse, PaperPerformance, QuoteResponse, StockSummary, Trade } from "@/types";

/** 모의투자 (SPEC 13.3). 로그인 필요. */
export default function PaperView() {
  const [token, ready] = useToken();
  if (!ready) return <Skeleton lines={6} />;
  if (!token) return <AuthGate />;
  return <PaperApp token={token} />;
}

function PaperApp({ token }: { token: string }) {
  const toast = useToast();
  const params = useSearchParams();
  const [code, setCode] = useState(params?.get("code") ?? "005930");
  const [picked, setPicked] = useState<number | undefined>();
  const [version, setVersion] = useState(0);
  const [liveTotal, setLiveTotal] = useState<{ total_value: number; return_pct: number } | null>(null);

  const stocks = useSWR<StockSummary[]>(["/stocks", null], swrFetcher);
  const accounts = useSWR<Accounts>(["/paper/accounts/me", token], swrFetcher, { shouldRetryOnError: false });
  const quote = useSWR<QuoteResponse>([`/stocks/${code}/quote`, null], swrFetcher);
  const book = useSWR<OrderbookResponse>([`/stocks/${code}/orderbook`, null], swrFetcher);
  const perfMe = useSWR<PaperPerformance>(
    accounts.data ? [`/paper/performance?account=me&v=${version}`, token] : null,
    swrFetcher,
  );
  const perfAi = useSWR<PaperPerformance>(accounts.data ? ["/paper/performance?account=ai", token] : null, swrFetcher);
  const { quotes, books, status, conn } = useQuotes([code]);

  const names = useMemo(() => Object.fromEntries((stocks.data ?? []).map((s) => [s.code, s.name])), [stocks.data]);
  const refresh = useCallback(() => {
    setVersion((v) => v + 1);
    accounts.mutate();
  }, [accounts]);

  // 체결/주문 이벤트와 평가금액 푸시 (SPEC 11, FR-PT-8)
  const paperConn = usePaperSocket(accounts.data ? token : null, (msg) => {
    if (msg.type === "trade") {
      const t = msg.trade as Trade;
      toast(
        `체결 · ${names[t.code] ?? t.code} ${t.side === "BUY" ? "매수" : "매도"} ${won(t.qty)}주 @ ${krw(t.price)}`,
        t.side === "BUY" ? "up" : "dn",
      );
      refresh();
    } else if (msg.type === "order_update") {
      refresh();
    } else if (msg.type === "portfolio" && typeof msg.total_value === "number") {
      setLiveTotal({ total_value: msg.total_value, return_pct: msg.return_pct as number });
    }
  });

  if (accounts.error instanceof ApiError && accounts.error.status === 401) {
    // 액세스 토큰 만료: 리프레시 쿠키로 다시 받는다. 안 되면 로그인 화면으로 바뀐다.
    return <ErrorBox message="로그인이 만료됐습니다." onRetry={onUnauthorized} />;
  }
  if (accounts.error instanceof ApiError && accounts.error.code === "ACCOUNT_NOT_FOUND") {
    return <AccountSetup token={token} onDone={refresh} />;
  }
  if (accounts.error) return <ErrorBox message="계좌를 불러오지 못했습니다." onRetry={() => accounts.mutate()} />;
  if (!accounts.data) return <Skeleton lines={8} />;

  const me = accounts.data.user;
  const ai = accounts.data.ai;
  const liveQ = quotes[code];
  const price = liveQ?.price ?? quote.data?.price;
  const changePct = liveQ?.change_pct ?? quote.data?.change_pct ?? 0;
  const prevClose = quote.data ? quote.data.price - quote.data.change : undefined;
  const ob = books[code] ?? book.data;
  const total = liveTotal ?? { total_value: me.total_value, return_pct: me.return_pct };

  const resetAccount = async () => {
    if (!window.confirm("미체결 주문을 모두 취소하고 새 계좌로 시작합니다. 이전 내역은 보존됩니다. 계속할까요?"))
      return;
    try {
      await apiRequest("/paper/accounts/reset", { method: "POST", token, body: {} });
      toast("계좌를 초기화했습니다");
      setLiveTotal(null);
      refresh();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "초기화하지 못했습니다", "err");
    }
  };

  return (
    <>
      <DisconnectBanner conn={conn} />
      <div className="paper-grid">
        {/* 좌: 종목 선택 + 호가창 */}
        <div className="panel">
          <div className="panel-h">
            <select
              className="input"
              style={{ width: "auto", maxWidth: "60%" }}
              value={code}
              onChange={(e) => {
                setCode(e.target.value);
                setPicked(undefined);
              }}
              aria-label="종목 선택"
            >
              {(stocks.data ?? [{ code, name: code }]).map((s) => (
                <option key={s.code} value={s.code}>
                  {s.name} ({s.code})
                </option>
              ))}
            </select>
            <ConnectionBadge conn={conn} />
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginBottom: 8 }}>
            <span className={`value ${tone(changePct)}`} style={{ fontSize: 20 }}>
              {price != null ? won(price) : "-"}
            </span>
            <span className={tone(changePct)}>
              {arrow(changePct)} {pctValue(changePct)}
            </span>
          </div>
          {ob ? (
            <Orderbook asks={ob.asks} bids={ob.bids} price={price} onPick={setPicked} />
          ) : (
            <Skeleton lines={10} height={18} />
          )}
          <div className="label" style={{ marginTop: 6 }}>
            호가를 누르면 주문 가격으로 들어갑니다.
          </div>
        </div>

        {/* 중: 주문 패널 */}
        <OrderPanel
          token={token}
          code={code}
          name={names[code] ?? code}
          refPrice={price}
          prevClose={prevClose}
          pickedPrice={picked}
          availableCash={me.available_cash}
          holding={me.holdings.find((h) => h.code === code)}
          marketOpen={status ? status.market === "open" : null}
          onPlaced={refresh}
        />

        {/* 우: 계좌 요약 */}
        <div className="panel">
          <div className="panel-h">
            <span>내 계좌</span>
            <span className={`chip ${paperConn === "open" ? "chip-live" : ""}`}>
              {paperConn === "open" ? "체결 알림 연결됨" : "체결 알림 연결 중"}
            </span>
          </div>
          <div className="label">총자산</div>
          <div className="stock-price" style={{ fontSize: 22 }}>
            {krw(total.total_value)}
          </div>
          <div className={tone(total.return_pct)} style={{ marginBottom: 10 }}>
            {pctValue(total.return_pct)} (초기 {krw(me.initial_cash)})
          </div>
          <div className="kv">
            <span className="label">현금</span>
            <span>{krw(me.cash)}</span>
          </div>
          <div className="kv">
            <span className="label">예약금(미체결 매수)</span>
            <span>{krw(me.reserved_cash)}</span>
          </div>
          <div className="kv">
            <span className="label">주문 가능 현금</span>
            <span>{krw(me.available_cash)}</span>
          </div>
          <div className="kv">
            <span className="label">주식 평가금액</span>
            <span>{krw(me.stock_value)}</span>
          </div>
          <div className="kv" style={{ borderTop: "0.5px solid var(--border)", marginTop: 6, paddingTop: 8 }}>
            <span className="label">AI 계좌</span>
            <span className={tone(ai.return_pct)}>
              {krw(ai.total_value)} ({pctValue(ai.return_pct)})
            </span>
          </div>
          <button className="btn btn-sm" style={{ marginTop: 10 }} onClick={resetAccount}>
            계좌 초기화
          </button>
        </div>
      </div>

      <PaperTabs token={token} holdings={me.holdings} names={names} version={version} onChanged={refresh} />

      <section className="panel">
        <div className="panel-h">
          <span>성과 · 나 vs KOSPI vs AI</span>
          <span style={{ display: "flex", gap: 10 }}>
            <span className="up">● 나</span>
            <span style={{ color: "var(--text-3)" }}>● KOSPI</span>
            <span style={{ color: "#7F77DD" }}>● AI</span>
          </span>
        </div>
        {perfMe.error ? (
          <ErrorBox message="성과를 불러오지 못했습니다." onRetry={() => perfMe.mutate()} />
        ) : !perfMe.data ? (
          <Skeleton lines={4} />
        ) : (
          <>
            <PerformanceChart me={perfMe.data} ai={perfAi.data} />
            <div className="grid-3" style={{ marginTop: 12 }}>
              <div>
                <div className="label">최대 낙폭(MDD)</div>
                <div className="value dn">{perfMe.data.mdd_pct.toFixed(2)}%</div>
              </div>
              <div>
                <div className="label">승률 (실현 매도 기준)</div>
                <div className="value">
                  {perfMe.data.win_rate == null ? "-" : `${perfMe.data.win_rate.toFixed(1)}%`}
                </div>
              </div>
              <div>
                <div className="label">매매 횟수 · 회전율</div>
                <div className="value">
                  {perfMe.data.trade_count}회 · {perfMe.data.turnover.toFixed(2)}
                </div>
              </div>
            </div>
          </>
        )}
      </section>
      <AiOrders token={token} names={names} version={version} />
      {!stocks.data?.length && !stocks.isLoading && <Empty title="거래할 수 있는 종목이 없습니다" />}
    </>
  );
}
