"use client";

import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import type { Candle, ChartMarker } from "@/types";

export interface Forecast {
  dates: string[]; // 미래 5거래일
  q10: number[];
  q50: number[];
  q90: number[];
}

const RANGES = [
  { key: "1M", bars: 21 },
  { key: "3M", bars: 63 },
  { key: "6M", bars: 126 },
] as const;

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export default function PriceChart({
  candles,
  forecast,
  markers = [],
  height = 340,
}: {
  candles: Candle[];
  forecast?: Forecast;
  markers?: ChartMarker[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [range, setRange] = useState<(typeof RANGES)[number]["key"]>("6M");

  useEffect(() => {
    if (!ref.current) return;
    const up = css("--up");
    const dn = css("--dn");
    const chart = createChart(ref.current, {
      height,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: css("--text-2"),
        fontSize: 11,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: css("--border") } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.06, bottom: 0.26 } },
      timeScale: { borderVisible: false, rightOffset: 8 },
      crosshair: { mode: CrosshairMode.Normal },
    });
    chartRef.current = chart;

    // 국내 관례: 상승 빨강 / 하락 파랑
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

    const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" },
      priceScaleId: "vol",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(
      candles.map((c) => ({
        time: c.time as Time,
        value: c.volume,
        color: (c.close >= c.open ? up : dn) + "73",
      })),
    );

    if (markers.length) {
      const m: SeriesMarker<Time>[] = markers
        .filter((x) => candles.some((c) => c.time === x.date))
        .sort((a, b) => a.date.localeCompare(b.date))
        .map((x) => ({
          time: x.date as Time,
          position: "aboveBar",
          color: x.type === "news" ? "#7F77DD" : "#EF9F27",
          shape: x.type === "news" ? "circle" : "arrowDown",
          text: x.type === "news" ? "뉴스" : "공시",
        }));
      cs.setMarkers(m);
    }

    if (forecast) {
      const last = candles[candles.length - 1];
      const line = (vals: number[], color: string, style = LineStyle.Solid, width: 1 | 2 = 1) => {
        const s = chart.addLineSeries({
          color,
          lineWidth: width,
          lineStyle: style,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        });
        s.setData([
          { time: last.time as Time, value: last.close },
          ...forecast.dates.map((d, i) => ({ time: d as Time, value: vals[i] })),
        ]);
      };
      line(forecast.q90, up, LineStyle.Solid, 2);
      line(forecast.q10, dn, LineStyle.Solid, 2);
      line(forecast.q50, css("--text"), LineStyle.Dashed, 2);
    }

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [candles, forecast, markers, height]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const bars = RANGES.find((r) => r.key === range)?.bars ?? 126;
    const total = candles.length;
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, total - bars), to: total + 8 });
  }, [range, candles.length]);

  return (
    <div>
      <div className="panel-h">
        <span>일봉 · 예측 콘 (5거래일, q10 · q50 · q90)</span>
        <span style={{ display: "flex", gap: 10 }}>
          {RANGES.map((r) => (
            <button
              key={r.key}
              onClick={() => setRange(r.key)}
              style={{
                all: "unset",
                cursor: "pointer",
                fontWeight: range === r.key ? 600 : 400,
                color: range === r.key ? "var(--text)" : "var(--text-2)",
              }}
            >
              {r.key}
            </button>
          ))}
        </span>
      </div>
      <div ref={ref} style={{ width: "100%", height }} />
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
      </div>
    </div>
  );
}
