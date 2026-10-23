"use client";

import { useState } from "react";
import useSWR from "swr";

import { CalibrationChart, HitTrend } from "@/components/performance/Charts";
import { Empty, ErrorBox, Skeleton } from "@/components/ui/States";
import { ApiError, swrEnvelope, type Envelope } from "@/lib/api";
import type { Performance, Strategy, StrategyMetrics } from "@/types";

type MetricKey = Exclude<keyof StrategyMetrics, "n">;

const STRATEGIES: { key: Strategy; label: string }[] = [
  { key: "model", label: "모델" },
  { key: "always_up", label: "항상 상승" },
  { key: "momentum", label: "5일 모멘텀" },
  { key: "kospi", label: "KOSPI 추종" },
];

const METRICS: { key: MetricKey; label: string; fmt: (v: number) => string; help: string }[] = [
  { key: "direction_accuracy", label: "방향 정확도", fmt: (v) => `${(v * 100).toFixed(1)}%`, help: "5거래일 뒤 오를지 내릴지를 맞힌 비율. 50%가 동전 던지기 수준입니다." },
  { key: "brier", label: "Brier 점수", fmt: (v) => v.toFixed(4), help: "확률 예측의 오차. 낮을수록 좋습니다. 항상 50%라고 하면 0.25입니다." },
  { key: "pinball_q50", label: "Pinball(중앙값)", fmt: (v) => v.toFixed(4), help: "중앙 예측과 실제 수익률의 차이. 낮을수록 좋습니다." },
  { key: "pinball_q10", label: "Pinball(하단)", fmt: (v) => v.toFixed(4), help: "하단(10%) 예측의 정확도. 낮을수록 좋습니다." },
  { key: "pinball_q90", label: "Pinball(상단)", fmt: (v) => v.toFixed(4), help: "상단(90%) 예측의 정확도. 낮을수록 좋습니다." },
  { key: "range_coverage", label: "범위 적중률", fmt: (v) => `${(v * 100).toFixed(1)}%`, help: "실제 수익률이 예상 범위(10~90%) 안에 든 비율. 목표는 80%입니다." },
  { key: "ic", label: "IC", fmt: (v) => v.toFixed(3), help: "같은 날 종목들 사이에서 예측 순위와 실제 순위가 얼마나 같은지(-1~1). 0에 가까우면 순위를 못 맞힌 것입니다." },
];

/** 성적표 (SPEC 13.4). 베이스라인보다 나쁜 지표도 그대로 보여준다 (FR-PF-6). */
export default function PerformanceView() {
  const [scope, setScope] = useState<"live" | "offline">("live");
  const [window, setWindow] = useState<"20" | "60" | "250" | "all">("60");
  const { data, error, isLoading, mutate } = useSWR<Envelope<Performance>>(
    [`/model/performance?scope=${scope}&window=${window}`, null],
    swrEnvelope,
  );
  const perf = data?.data;
  const note = data?.meta.note as string | undefined;

  return (
    <>
      <div className="panel" style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", justifyContent: "space-between" }}>
        <span className="value">모델 성적표</span>
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <span className="seg" role="group" aria-label="결과 범위">
            <button aria-pressed={scope === "live"} onClick={() => setScope("live")}>
              실제 운영(live)
            </button>
            <button aria-pressed={scope === "offline"} onClick={() => setScope("offline")}>
              학습 검증(offline)
            </button>
          </span>
          <span className="seg" role="group" aria-label="기간">
            {(["20", "60", "250", "all"] as const).map((w) => (
              <button key={w} aria-pressed={(scope === "offline" ? "all" : window) === w} disabled={scope === "offline"} onClick={() => setWindow(w)}>
                {w === "all" ? "전체" : `${w}일`}
              </button>
            ))}
          </span>
        </span>
      </div>

      {isLoading ? (
        <div className="panel">
          <Skeleton lines={6} />
        </div>
      ) : error ? (
        error instanceof ApiError && error.status === 404 ? (
          <Empty title="설정된 예측 모델이 없습니다">MODEL_VERSION 을 설정하면 성적표가 나타납니다.</Empty>
        ) : (
          <ErrorBox message="성적표를 불러오지 못했습니다." onRetry={() => mutate()} />
        )
      ) : perf ? (
        <>
          <div className="label">
            {scope === "live"
              ? "실제 운영 중 예측 후 5거래일이 지나 결과가 확인된 것만 집계합니다."
              : "학습 때 시간 순서대로 나눠 검증한 결과(walk-forward)입니다. 실제 운영 결과와 다를 수 있습니다."}{" "}
            모델 {perf.model_version} · 표본 {perf.n.toLocaleString("ko-KR")}건
          </div>
          {note && perf.n === 0 ? (
            <div className="panel">
              <Empty title="아직 집계할 결과가 없습니다">{note}</Empty>
            </div>
          ) : (
            <>
              <section className="panel">
                <div className="panel-h">
                  <span>모델 vs 베이스라인</span>
                  <span>★ 지표별 가장 좋은 쪽</span>
                </div>
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>지표</th>
                        {STRATEGIES.map((s) => (
                          <th key={s.key}>{s.label}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {METRICS.map((m) => (
                        <tr key={m.key}>
                          <td title={m.help}>{m.label}</td>
                          {STRATEGIES.map((s) => {
                            const v = perf.strategies[s.key]?.[m.key];
                            const best = perf.best[m.key] === s.key;
                            return (
                              <td key={s.key} className={best ? "metric-best" : ""}>
                                {v == null ? "-" : m.fmt(v)}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="grid-2">
                <div className="panel">
                  <div className="panel-h">
                    <span>보정 곡선 (상승확률)</span>
                    <span>점선 = 완벽한 보정</span>
                  </div>
                  <CalibrationChart bins={perf.calibration_up} />
                </div>
                <div className="panel">
                  <div className="panel-h">
                    <span>범위 적중률</span>
                    <span>목표 80%</span>
                  </div>
                  {STRATEGIES.map((s) => {
                    const v = perf.strategies[s.key]?.range_coverage;
                    return (
                      <div key={s.key} style={{ display: "grid", gridTemplateColumns: "90px 1fr 50px", gap: 8, alignItems: "center", margin: "8px 0" }}>
                        <span className="label">{s.label}</span>
                        <div className="axis" style={{ height: 10 }}>
                          <b style={{ left: "80%" }} />
                          <i style={{ left: 0, width: `${(v ?? 0) * 100}%`, background: s.key === "model" ? "var(--up)" : "var(--text-3)" }} />
                        </div>
                        <span style={{ textAlign: "right" }}>{v == null ? "-" : `${(v * 100).toFixed(0)}%`}</span>
                      </div>
                    );
                  })}
                  {scope === "live" && perf.daily.length > 1 && (
                    <>
                      <div className="panel-h" style={{ marginTop: 16 }}>
                        <span>누적 방향 적중률 추이</span>
                        <span>점 = 그날 적중률, 선 = 누적</span>
                      </div>
                      <HitTrend daily={perf.daily} />
                    </>
                  )}
                </div>
              </section>
            </>
          )}
          <section className="panel">
            <div className="panel-h">
              <span>지표 읽는 법</span>
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8, fontSize: 13 }}>
              {METRICS.map((m) => (
                <li key={m.key}>
                  <b>{m.label}</b>: {m.help}
                </li>
              ))}
              <li>
                <b>베이스라인</b>: 항상 상승(늘 오른다고 예측), 5일 모멘텀(직전 5일 방향이 이어진다), KOSPI 추종(시장 방향을
                따른다). 모델이 이보다 못하면 예측을 그대로 믿지 마세요.
              </li>
            </ul>
          </section>
        </>
      ) : null}
    </>
  );
}
