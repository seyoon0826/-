"use client";

import { useState } from "react";

import { setToken } from "@/lib/auth";

/** 로그인 필요 (FR-AU-3). 가입/로그인 화면은 Phase 8 — 개발 중에는 발급한 토큰을 붙여 넣어 쓴다. */
export default function AuthGate() {
  const [value, setValue] = useState("");
  const dev = process.env.NODE_ENV !== "production";
  return (
    <div className="panel" style={{ display: "grid", gap: 12, maxWidth: 520 }}>
      <span className="value">모의투자는 로그인이 필요합니다</span>
      <p className="label" style={{ margin: 0 }}>
        예측·시세·성적표는 로그인 없이 볼 수 있습니다. 가입/로그인 화면은 다음 단계(Phase 8)에서 추가됩니다.
      </p>
      {dev && (
        <form
          style={{ display: "grid", gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) setToken(value.trim());
          }}
        >
          <label className="field">
            <span className="label">개발용: 액세스 토큰 붙여넣기</span>
            <input className="input" value={value} onChange={(e) => setValue(e.target.value)} placeholder="eyJhbGciOi..." />
          </label>
          <button className="btn" type="submit">
            토큰으로 시작
          </button>
        </form>
      )}
    </div>
  );
}
