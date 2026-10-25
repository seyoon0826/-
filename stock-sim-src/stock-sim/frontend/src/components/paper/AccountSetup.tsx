"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { useToast } from "@/components/ui/Toast";
import { ApiError, apiRequest } from "@/lib/api";
import { won } from "@/lib/format";

const OPTIONS = [10_000_000, 30_000_000, 50_000_000, 100_000_000];

/** 계좌가 없으면 초기 자금 선택 (FR-PT-1). 같은 자금·같은 시작일로 AI 계좌도 함께 만든다. */
export default function AccountSetup({ token, onDone }: { token: string; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  // 소개 페이지의 "내 AI와 [금액]으로 겨뤄 볼게요"에서 고른 금액 (/paper?cash=30000000)
  const picked = Number(useSearchParams()?.get("cash"));
  const chosen = OPTIONS.includes(picked) ? picked : null;
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
  const label = (c: number) => (c >= 100_000_000 ? "1억" : `${c / 10_000_000}천만`);
  return (
    <div className="panel" style={{ display: "grid", gap: 14, maxWidth: 560 }}>
      <h1 style={{ margin: 0, fontSize: 22 }}>처음 자금을 고르세요</h1>
      <p className="label" style={{ margin: 0 }}>
        같은 금액으로 내 AI 계좌가 함께 열립니다. AI는 매일 09:05 예측 순위 상위 5종목을 같은 금액씩 사고, 수익률이
        나란히 기록됩니다.
      </p>
      {chosen && (
        <button className="btn btn-primary" disabled={busy} onClick={() => create(chosen)}>
          {label(chosen)} 원으로 계좌 만들기
        </button>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8 }}>
        {OPTIONS.filter((c) => c !== chosen).map((c) => (
          <button
            key={c}
            className="btn"
            disabled={busy}
            onClick={() => create(c)}
            style={{ justifyContent: "center" }}
          >
            {label(c)} 원
          </button>
        ))}
      </div>
    </div>
  );
}
