"use client";

import { ColorType, createChart, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";

import { Empty } from "@/components/ui/States";
import { cssVar as css } from "@/lib/theme";
import type { PaperPerformance } from "@/types";


/** 수익률 곡선: 나 vs KOSPI vs AI (SPEC 13.3, FR-PT-7). 매일 16:30 스냅샷 기준. */
export default function PerformanceChart({ me, ai }: { me?: PaperPerformance; ai?: PaperPerformance }) {
  const ref = useRef<HTMLDivElement>(null);
  const has = (me?.curve.length ?? 0) > 0;

  useEffect(() => {
    if (!ref.current || !me || !has) return;
    const chart = createChart(ref.current, {
      height: 240,
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--text-2"), fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: css("--border") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
    });
    const line = (color: string, data: { time: string; value: number }[], title: string) => {
      const s = chart.addLineSeries({ color, lineWidth: 2, title, priceFormat: { type: "custom", formatter: (v: number) => `${v.toFixed(2)}%` } });
      s.setData(data.map((d) => ({ time: d.time as Time, value: d.value })));
    };
    line(css("--up"), me.curve.map((c) => ({ time: c.date, value: c.return_pct })), "나");
    line(css("--text-3"), me.curve.filter((c) => c.kospi_return_pct != null).map((c) => ({ time: c.date, value: c.kospi_return_pct as number })), "KOSPI");
    if (ai?.curve.length) line("#7F77DD", ai.curve.map((c) => ({ time: c.date, value: c.return_pct })), "AI");
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [me, ai, has]);

  if (!has) return <Empty title="아직 수익률 곡선이 없습니다">매 거래일 16:30 에 그날의 총자산이 기록됩니다.</Empty>;
  return <div ref={ref} style={{ width: "100%", height: 240 }} />;
}
