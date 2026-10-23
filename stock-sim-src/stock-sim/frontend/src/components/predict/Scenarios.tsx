"use client";

import { useState } from "react";

import type { Scenarios as ScenarioData } from "@/types";

const TABS = [
  { key: "summary", label: "요약", cls: "" },
  { key: "bull", label: "강세", cls: "up" },
  { key: "base", label: "기본", cls: "" },
  { key: "bear", label: "약세", cls: "dn" },
] as const;

/** 시나리오 탭 (SPEC 13.2-5). 템플릿 문장이면 작게 표시. */
export default function Scenarios({ data }: { data: ScenarioData }) {
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("summary");
  return (
    <div className="panel">
      <div className="panel-h">
        <span>시나리오</span>
        {data.generated_by === "template" && (
          <span className="chip" title="LLM 을 쓸 수 없거나 검증을 통과하지 못해 고정 문장으로 만들었습니다">
            템플릿 문장
          </span>
        )}
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={t.cls} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      <p role="tabpanel" style={{ margin: 0, lineHeight: 1.7 }}>
        {data[tab]}
      </p>
    </div>
  );
}
