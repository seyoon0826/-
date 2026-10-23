"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import { login, signup } from "@/lib/auth";

/** 로그인/가입 (FR-AU-1/3). 예측·시세·성적표는 로그인 없이, 모의투자는 로그인 필요. */
export default function AuthGate() {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await (mode === "login" ? login(email, password) : signup(email, password));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "요청에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel auth-card">
      <div className="panel-h">
        <span className="value">{mode === "login" ? "로그인" : "회원가입"}</span>
        <span className="seg" role="group" aria-label="로그인 또는 가입">
          <button type="button" aria-pressed={mode === "login"} onClick={() => setMode("login")}>
            로그인
          </button>
          <button type="button" aria-pressed={mode === "signup"} onClick={() => setMode("signup")}>
            가입
          </button>
        </span>
      </div>
      <p className="label" style={{ margin: 0 }}>
        모의투자는 로그인이 필요합니다. 예측·시세·성적표는 로그인 없이 볼 수 있습니다.
      </p>
      <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
        <label className="field">
          <span className="label">이메일</span>
          <input
            className="input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="label">비밀번호 {mode === "signup" && "(8자 이상)"}</span>
          <input
            className="input"
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            minLength={mode === "signup" ? 8 : undefined}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <div className="banner" role="alert">
            {error}
          </div>
        )}
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "처리 중…" : mode === "login" ? "로그인" : "가입하고 시작"}
        </button>
      </form>
    </div>
  );
}
