import Link from "next/link";
import { notFound } from "next/navigation";

import PriceChart, { type Forecast } from "@/components/chart/PriceChart";
import FactorCards from "@/components/predict/FactorCards";
import { ProbabilityGauge, RangeCard } from "@/components/predict/PredictionPanel";
import Scenarios from "@/components/predict/Scenarios";
import TrackRecord from "@/components/predict/TrackRecord";
import { arrow, pct, tone, won } from "@/lib/format";
import { candles, findStock, forecastDates, markers, prediction, trackRecord } from "@/lib/mock";

export default function PredictDetail({ params }: { params: { code: string } }) {
  const stock = findStock(params.code);
  if (!stock) notFound();

  const pred = prediction(stock.code);
  const cs = candles(stock.code, stock.price);
  const last = cs[cs.length - 1];
  // 목록(STOCKS)과 같은 등락률을 쓰도록 전일 종가를 역산
  const prevClose = Math.round(last.close / (1 + stock.chg / 100));
  const dayChange = last.close - prevClose;

  // 콘: 시간이 갈수록 불확실성이 √t 로 커진다고 가정한 표시용 보간 (모델 출력은 5일 후 분위수만)
  const dates = forecastDates();
  const band = (q: number) => dates.map((_, i) => Math.round(pred.close * (1 + q * Math.sqrt((i + 1) / 5))));
  const forecast: Forecast = {
    dates,
    q10: band(pred.range.q10),
    q50: band(pred.range.q50),
    q90: band(pred.range.q90),
  };

  return (
    <>
      <section className="panel stock-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 18, fontWeight: 600 }}>{stock.name}</span>
          <span className="label">{stock.code} · KOSPI</span>
          <span className={`stock-price ${tone(dayChange)}`}>{won(last.close)}</span>
          <span className={tone(dayChange)}>
            {arrow(dayChange)} {won(Math.abs(dayChange))} &nbsp;{pct(dayChange / prevClose)}
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
        <PriceChart candles={cs} forecast={forecast} markers={markers(cs)} />
      </section>

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
