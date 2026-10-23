"use client";

import { useState } from "react";

import { useToast } from "@/components/ui/Toast";
import { ApiError, apiRequest } from "@/lib/api";
import { won } from "@/lib/format";

const OPTIONS = [10_000_000, 30_000_000, 50_000_000, 100_000_000];

/** 계좌가 없으면 초기 자금 선택 (FR-PT-1). 같은 자금·같은 시작일로 AI 계좌도 함께 만든다. */
export default function AccountSetup({ token, onDone }: { token: string; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const create = async (cash: number) => {
    setBusy(true);
    try {
      await apiRequest("/paper/accounts", { method: "POST", token, body: { initial_cash: cash } });
      toast(`${won(cash)}원으로 모의투자 계좌를 만들었습니다`);
      onDone();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "계좌를 만들지 못했습니다", "err");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="panel" style={{ display: "grid", gap: 12, maxWidth: 560 }}>
      <span className="value">초기 자금을 고르세요</span>
      <p className="label" style={{ margin: 0 }}>
        같은 금액으로 AI 계좌도 함께 만들어져, 예측대로 매매하는 AI 와 수익률을 비교할 수 있습니다.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8 }}>
        {OPTIONS.map((c) => (
          <button key={c} className="btn" disabled={busy} onClick={() => create(c)} style={{ justifyContent: "center" }}>
            {c >= 100_000_000 ? "1억" : `${c / 10_000_000}천만`} 원
          </button>
        ))}
      </div>
    </div>
  );
}
