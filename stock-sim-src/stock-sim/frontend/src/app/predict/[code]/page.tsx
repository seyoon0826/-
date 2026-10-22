import Link from "next/link";
import { notFound } from "next/navigation";

import SourceChip from "@/components/SourceChip";
import PriceChart, { type Forecast } from "@/components/chart/PriceChart";
import FactorCards from "@/components/predict/FactorCards";
import { ProbabilityGauge, RangeCard } from "@/components/predict/PredictionPanel";
import Scenarios from "@/components/predict/Scenarios";
import TrackRecord from "@/components/predict/TrackRecord";
import { ApiError, apiGet } from "@/lib/api";
import { arrow, pct, tone, won } from "@/lib/format";
import { candles, findStock, forecastDates, markers, prediction, trackRecord } from "@/lib/mock";
import type { Candle, ChartMarker, ChartResponse, QuoteResponse, StockSummary } from "@/types";

async function loadReal(code: string) {
  const [chart, quote, list] = await Promise.all([
    apiGet<ChartResponse>(`/stocks/${code}/chart?range=6M`),
    apiGet<QuoteResponse>(`/stocks/${code}/quote`),
    apiGet<StockSummary[]>("/stocks"),
  ]);
  const cs: Candle[] = chart.candles.map((c) => ({ ...c, time: c.date }));
  return { name: list.find((s) => s.code === code)?.name ?? code, cs, marks: chart.markers, quote };
}

export default async function PredictDetail({ params }: { params: { code: string } }) {
  let live = true;
  let name: string, cs: Candle[], marks: ChartMarker[], price: number, change: number, changePct: number;
  try {
    const r = await loadReal(params.code);
    ({ name, cs, marks } = r);
    ({ price, change, change_pct: changePct } = r.quote);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    const stock = findStock(params.code); // API 가 꺼져 있으면 예시 데이터로 디자인만 보여준다
    if (!stock) notFound();
    live = false;
    name = stock.name;
    cs = candles(stock.code, stock.price);
    marks = markers(cs);
    price = stock.price;
    changePct = stock.chg;
    change = Math.round(price - price / (1 + stock.chg / 100));
  }
  const code = params.code;
  const last = cs[cs.length - 1];
  const pred = prediction(code, price); // 예측은 Phase 5~6 전까지 예시 데이터

  // 콘: 시간이 갈수록 불확실성이 √t 로 커진다고 가정한 표시용 보간 (모델 출력은 5일 후 분위수만)
  const dates = forecastDates(last.time);
  const band = (q: number) => dates.map((_, i) => Math.round(pred.close * (1 + q * Math.sqrt((i + 1) / 5))));
  const forecast: Forecast = {
    dates,
    q10: band(pred.range.q10),
    q50: band(pred.range.q50),
    q90: band(pred.range.q90),
  };
  const stock = { name, code };
  const dayChange = change;
  const prevClose = price - change;

  return (
    <>
      <section className="panel stock-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 18, fontWeight: 600 }}>{stock.name}</span>
          <span className="label">{stock.code} · KOSPI</span>
          <span className={`stock-price ${tone(dayChange)}`}>{won(price)}</span>
          <span className={tone(dayChange)}>
            {arrow(dayChange)} {won(Math.abs(dayChange))} &nbsp;{pct(prevClose ? dayChange / prevClose : changePct / 100)}
          </span>
        </div>
        <div className="stats">
          <div>
            <div className="label">고가</div>
            <div className="value">{won(last.high)}</div>
          </div>
          <div>
            <div className="label">저가</div>
            <div className="value">{won(last.low)}</div>
          </div>
          <div>
            <div className="label">거래량</div>
            <div className="value">{(last.volume / 1e6).toFixed(1)}M</div>
          </div>
          <div>
            <div className="label">예측 기준일</div>
            <div className="value">{pred.as_of_date}</div>
          </div>
        </div>
      </section>

      <section className="panel">
        <PriceChart candles={cs} forecast={forecast} markers={marks} />
      </section>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <SourceChip live={live} label={live ? "가격·차트 실데이터" : "가격·차트 예시 데이터"} />
        <SourceChip live={false} label="예측·근거·시나리오: 예시 데이터 (Phase 5~6)" />
      </div>

      <section className="grid-2">
        <ProbabilityGauge p_up={pred.p_up} p_outperform={pred.p_outperform} />
        <RangeCard close={pred.close} range={pred.range} />
      </section>

      <section className="grid-2">
        <FactorCards factors={pred.factors} />
        <TrackRecord data={trackRecord()} />
      </section>

      <Scenarios data={pred.scenarios} />

      <section className="panel">
        <div className="panel-h">
          <span>관련 뉴스</span>
          <span>기준일 직전 3거래일</span>
        </div>
        {pred.news.map((n) => (
          <a className="news-item" href={n.url} key={n.title}>
            <span>{n.title}</span>
            <span className="label">{n.published_at.slice(5, 10)}</span>
          </a>
        ))}
      </section>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <span className="label">모델 {pred.model_version}</span>
        <Link className="btn" href={`/paper?code=${stock.code}`}>
          이 종목 모의 매수 ↗
        </Link>
      </div>
    </>
  );
}
