"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";

import PriceChart, { nextBusinessDays, type Forecast, type Range } from "@/components/chart/PriceChart";
import FactorCards from "@/components/predict/FactorCards";
import ForecastHero from "@/components/predict/Forecast";
import Scenarios from "@/components/predict/Scenarios";
import TrackRecord from "@/components/predict/TrackRecord";
import ConnectionBadge, { DisconnectBanner } from "@/components/ui/ConnectionBadge";
import { Empty, ErrorBox, Skeleton } from "@/components/ui/States";
import { ApiError, swrEnvelope, swrFetcher, type Envelope } from "@/lib/api";
import { kstDateTime, pctValue, tone, won } from "@/lib/format";
import { useQuotes } from "@/lib/ws";
import type {
  Candle,
  ChartResponse,
  HistoryItem,
  HistorySummary,
  Prediction,
  QuoteResponse,
  StockSummary,
} from "@/types";

/** 예측 상세 (SPEC 13.2): 헤더(실시간) / 차트 / 예측 카드 / 근거 / 시나리오 / 뉴스 / 예측 기록 / 모의 매수 */
export default function PredictDetail({ code }: { code: string }) {
  const [range, setRange] = useState<Range>("6M");
  const stocks = useSWR<StockSummary[]>(["/stocks", null], swrFetcher);
  const chart = useSWR<ChartResponse>([`/stocks/${code}/chart?range=${range}`, null], swrFetcher, {
    keepPreviousData: true,
  });
  const quote = useSWR<QuoteResponse>([`/stocks/${code}/quote`, null], swrFetcher);
  const pred = useSWR<Envelope<Prediction>>([`/stocks/${code}/prediction`, null], swrEnvelope, {
    shouldRetryOnError: false,
  });
  const hist = useSWR<Envelope<HistoryItem[]>>([`/stocks/${code}/prediction/history?limit=20`, null], swrEnvelope);
  const { quotes, status, conn } = useQuotes([code]);

  const name = stocks.data?.find((s) => s.code === code)?.name ?? code;
  const live = quotes[code];
  const price = live?.price ?? quote.data?.price;
  const changePct = live?.change_pct ?? quote.data?.change_pct ?? 0;
  const prevClose = quote.data ? quote.data.price - quote.data.change : undefined;

  const candles: Candle[] = useMemo(
    () => (chart.data?.candles ?? []).map((c) => ({ ...c, time: c.date })),
    [chart.data],
  );
  const last = candles[candles.length - 1];
  const p = pred.data?.data;
  const forecast: Forecast | undefined = useMemo(() => {
    if (!p || !last || p.as_of_date !== last.time) return undefined; // 차트 마지막 봉이 기준일일 때만 이어 그린다
    const dates = nextBusinessDays(p.as_of_date, p.horizon);
    // 모델 출력은 5일 뒤 분위수뿐: 중간 날짜는 √t 로 보간한 표시용 경로
    const band = (q: number) => dates.map((_, i) => Math.round(p.close * (1 + q * Math.sqrt((i + 1) / dates.length))));
    return { dates, q10: band(p.range.q10), q50: band(p.range.q50), q90: band(p.range.q90) };
  }, [p, last]);

  const notFound = quote.error instanceof ApiError && quote.error.status === 404;
  if (notFound) return <Empty title="알 수 없는 종목입니다">종목 코드를 확인하세요.</Empty>;
  const predMissing = pred.error instanceof ApiError && pred.error.code === "PREDICTION_NOT_FOUND";

  const forecastBlock = pred.isLoading ? (
    <div className="panel">
      <Skeleton lines={4} />
    </div>
  ) : predMissing ? (
    <div className="panel">
      <Empty title="아직 예측이 없습니다">다음 장 마감(15:40) 뒤에 만들어집니다.</Empty>
    </div>
  ) : pred.error ? (
    <ErrorBox message="예측을 불러오지 못했습니다." onRetry={() => pred.mutate()} />
  ) : p ? (
    <ForecastHero p={p} name={name} />
  ) : null;

  return (
    <>
      <DisconnectBanner conn={conn} />
      <header className="screen-head stock-head-row">
        <div>
          <h1>
            {name} <span className="code">{code}</span>
          </h1>
          {price != null ? (
            <p className="stock-quote">
              <span className="stock-price">{won(price)}원</span>{" "}
              <span className={tone(changePct)}>
                {prevClose != null ? `${price - prevClose >= 0 ? "+" : "-"}${won(Math.abs(price - prevClose))}원 ` : ""}
                ({pctValue(changePct)})
              </span>{" "}
              <span className="label">
                {live ? `실시간 ${kstDateTime(live.ts)}` : quote.data ? `${kstDateTime(quote.data.ts)} 기준` : ""}
              </span>
            </p>
          ) : (
            <span className="skeleton" style={{ display: "block", width: 220, height: 24, marginTop: 8 }} />
          )}
        </div>
        <div className="head-actions">
          <ConnectionBadge conn={conn} status={status} />
          <Link className="btn btn-primary btn-buy" href={`/paper?code=${code}`}>
            이 종목 모의 매수
          </Link>
        </div>
      </header>

      {forecastBlock}

      <section className="panel">
        {chart.error ? (
          <ErrorBox message="차트를 불러오지 못했습니다." onRetry={() => chart.mutate()} />
        ) : !chart.data ? (
          <Skeleton lines={6} height={40} />
        ) : candles.length === 0 ? (
          <Empty title="가격 데이터가 없습니다" />
        ) : (
          <PriceChart
            candles={candles}
            forecast={forecast}
            markers={chart.data.markers}
            live={live ?? null}
            range={range}
            onRangeChange={setRange}
          />
        )}
      </section>

      {p && (
        <>
          <section className="grid-2">
            <FactorCards factors={p.factors} />
            <TrackRecord
              items={hist.data?.data ?? []}
              summary={hist.data?.meta.summary as HistorySummary | undefined}
            />
          </section>
          {p.scenarios && <Scenarios data={p.scenarios} />}
          <section className="panel">
            <div className="panel-h">
              <span>관련 뉴스</span>
              <span>기준일 전 3거래일, 관련도 0.6 이상</span>
            </div>
            {p.news.length === 0 ? (
              <p className="label" style={{ margin: 0 }}>
                기준일 전 3거래일 동안 이 종목과 관련된 뉴스가 없습니다.
              </p>
            ) : (
              p.news.map((n) => (
                <a className="news-item" href={n.url} key={n.url} target="_blank" rel="noreferrer">
                  <span>{n.title}</span>
                  <span className="label">{n.published_at.slice(5, 10)}</span>
                </a>
              ))
            )}
          </section>
          <p className="label">
            {p.as_of_date} 종가로 만든 예측입니다. 모델 {p.model_version}
          </p>
        </>
      )}
    </>
  );
}
