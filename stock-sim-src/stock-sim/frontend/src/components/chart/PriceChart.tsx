"use client";

import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import { cssVar as css, useTheme } from "@/lib/theme";
import type { Candle, ChartMarker } from "@/types";

export const RANGES = ["1M", "3M", "6M", "1Y", "3Y"] as const;
export type Range = (typeof RANGES)[number];

export interface Forecast {
  dates: string[]; // 미래 5거래일 (주말 제외, 휴장일은 반영 못 함 — 표시용)
  q10: number[];
  q50: number[];
  q90: number[];
}

/** 다음 n 영업일 (YYYY-MM-DD). 휴장일은 모르므로 표시용. */
export function nextBusinessDays(from: string, n: number): string[] {
  const d = new Date(`${from}T00:00:00Z`);
  const out: string[] = [];
  while (out.length < n) {
    d.setUTCDate(d.getUTCDate() + 1);
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

/** 일봉 캔들 + 거래량 + 뉴스/공시 마커 + 예상 범위 음영 (SPEC 13.2-2, FR-ST-3/4). */
export default function PriceChart({
  candles,
  forecast,
  markers = [],
  live,
  range,
  onRangeChange,
  height = 340,
}: {
  candles: Candle[];
  forecast?: Forecast;
  markers?: ChartMarker[];
  live?: { price: number; ts: string } | null;
  range: Range;
  onRangeChange: (r: Range) => void;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lastBar = useRef<Candle | null>(null);
  const [picked, setPicked] = useState<ChartMarker[]>([]);
  const theme = useTheme(); // 모드가 바뀌면 차트를 새 색으로 다시 만든다

  useEffect(() => {
    if (!ref.current || candles.length === 0) return;
    const up = css("--up");
    const dn = css("--dn");
    const surface = css("--surface");
    const chart = createChart(ref.current, {
      height,
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--text-2"), fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: css("--border") } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.06, bottom: 0.26 } },
      timeScale: { borderVisible: false, rightOffset: 4 },
      crosshair: { mode: CrosshairMode.Normal },
    });
    chartRef.current = chart;

    // 예상 범위 음영: q90 까지 채운 뒤 q10 아래를 배경색으로 덮는다 (미래 구간에는 캔들이 없어 가려지는 것이 없다)
    if (forecast) {
      const last = candles[candles.length - 1];
      const pts = (vals: number[]) => [
        { time: last.time as Time, value: last.close },
        ...forecast.dates.map((d, i) => ({ time: d as Time, value: vals[i] })),
      ];
      const opts = {
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        lineWidth: 1 as const,
        priceFormat: { type: "price" as const, precision: 0, minMove: 1 }, // 가격축을 원 단위로
      };
      const hi = chart.addAreaSeries({ ...opts, lineColor: up, topColor: `${up}33`, bottomColor: `${up}33` });
      hi.setData(pts(forecast.q90));
      const mask = chart.addAreaSeries({ ...opts, lineColor: dn, topColor: surface, bottomColor: surface });
      mask.setData(pts(forecast.q10));
      const mid = chart.addLineSeries({ ...opts, color: css("--text"), lineStyle: LineStyle.Dashed, lineWidth: 2 });
      mid.setData(pts(forecast.q50));
    }

    const cs = chart.addCandlestickSeries({
      upColor: up,
      downColor: dn,
      borderUpColor: up,
      borderDownColor: dn,
      wickUpColor: up,
      wickDownColor: dn,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });
    cs.setData(candles.map((c) => ({ ...c, time: c.time as Time })));
    candleRef.current = cs;
    lastBar.current = candles[candles.length - 1];

    const vol = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(
      candles.map((c) => ({ time: c.time as Time, value: c.volume, color: `${c.close >= c.open ? up : dn}73` })),
    );

    const dates = new Set(candles.map((c) => c.time));
    const byDate = new Map<string, ChartMarker[]>();
    for (const m of markers) if (dates.has(m.date)) byDate.set(m.date, [...(byDate.get(m.date) ?? []), m]);
    const sm: SeriesMarker<Time>[] = [...byDate.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, ms]) => ({
        time: date as Time,
        position: "aboveBar",
        color: ms.some((m) => m.type === "disclosure") ? "#EF9F27" : "#7F77DD",
        shape: ms.some((m) => m.type === "disclosure") ? "arrowDown" : "circle",
        text: ms.length > 1 ? `${ms.length}` : ms[0].type === "news" ? "뉴스" : "공시",
      }));
    cs.setMarkers(sm);
    const onClick = (p: MouseEventParams) => setPicked(p.time ? (byDate.get(String(p.time)) ?? []) : []);
    chart.subscribeClick(onClick);
    chart.timeScale().fitContent();

    return () => {
      chart.unsubscribeClick(onClick);
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
    };
  }, [candles, forecast, markers, height, theme]);

  // 장중 실시간 가격으로 마지막 봉 갱신 (FR-ST-3)
  useEffect(() => {
    const cs = candleRef.current;
    const last = lastBar.current;
    if (!cs || !last || !live) return;
    const day = new Date(live.ts).toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
    if (day < last.time) return;
    const bar =
      day === last.time
        ? { ...last, close: live.price, high: Math.max(last.high, live.price), low: Math.min(last.low, live.price) }
        : { time: day, open: last.close, high: live.price, low: live.price, close: live.price, volume: 0 };
    lastBar.current = bar;
    cs.update({ ...bar, time: bar.time as Time });
  }, [live]);

  return (
    <div>
      <div className="panel-h">
        <span>일봉 차트. 오른쪽 음영은 5거래일 뒤 예상 범위(10~90%), 마커를 누르면 그날 뉴스와 공시를 봅니다</span>
        <span className="seg" role="group" aria-label="차트 기간">
          {RANGES.map((r) => (
            <button key={r} aria-pressed={range === r} onClick={() => onRangeChange(r)}>
              {r}
            </button>
          ))}
        </span>
      </div>
      <div ref={ref} style={{ width: "100%", height }} />
      {picked.length > 0 && (
        <div className="panel" style={{ marginTop: 8, padding: "10px 12px" }}>
          <div className="panel-h" style={{ marginBottom: 4 }}>
            <span>{picked[0].date} 뉴스·공시</span>
            <button className="btn btn-sm" onClick={() => setPicked([])}>
              닫기
            </button>
          </div>
          {picked.map((m) => (
            <a key={m.url} className="news-item" href={m.url} target="_blank" rel="noreferrer">
              <span>{m.title}</span>
              <span className="label">{m.type === "news" ? "뉴스" : "공시"}</span>
            </a>
          ))}
        </div>
      )}
      <div className="label" style={{ display: "flex", gap: 14, marginTop: 6, flexWrap: "wrap" }}>
        <span>
          <span className="up">■</span> 상승봉
        </span>
        <span>
          <span className="dn">■</span> 하락봉
        </span>
        <span>
          <span style={{ color: "#EF9F27" }}>▼</span> 공시
        </span>
        <span>
          <span style={{ color: "#7F77DD" }}>●</span> 뉴스
        </span>
        {forecast && <span>미래 날짜는 주말만 뺀 표시용 (휴장일 미반영), 중간 경로는 √t 보간</span>}
      </div>
    </div>
  );
}
