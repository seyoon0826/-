"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";

import { API_BASE, ApiError, swrFetcher } from "@/lib/api";
import { login, signup } from "@/lib/auth";

interface OAuthProvider {
  id: "naver" | "google";
  label: string;
}

// 콜백에서 돌아온 오류 코드 -> 사용자에게 보여줄 문장 (무엇이 잘못됐고 어떻게 하면 되는지)
const LOGIN_ERRORS: Record<string, string> = {
  cancelled: "로그인을 취소했습니다. 다시 시도하려면 버튼을 누르세요.",
  state_expired: "로그인 시간이 지났습니다. 다시 시도하세요.",
  state_mismatch: "로그인 요청을 확인하지 못했습니다. 다시 시도하세요.",
  token_exchange_failed: "로그인 서비스에서 인증을 받지 못했습니다. 잠시 뒤 다시 시도하세요.",
  profile_failed: "계정 정보를 받지 못했습니다. 잠시 뒤 다시 시도하세요.",
  provider_unreachable: "로그인 서비스에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.",
  provider_not_available: "이 로그인 방식은 지금 쓸 수 없습니다.",
};

function NaverMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" aria-hidden>
      <path fill="currentColor" d="M13.6 10.7 6.1 0H0v20h6.4V9.3L13.9 20H20V0h-6.4z" />
    </svg>
  );
}

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden>
      <path
        fill="#EA4335"
        d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.1 24.6c0-1.6-.1-3.1-.4-4.6H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17z"
      />
      <path
        fill="#FBBC05"
        d="M10.6 28.6c-.5-1.4-.8-2.9-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.1z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.7c-2.1 1.4-4.8 2.3-8.5 2.3-6.2 0-11.5-4.1-13.4-9.8l-7.9 6.1C6.6 42.6 14.6 48 24 48z"
      />
    </svg>
  );
}

/** 로그인/가입 (FR-AU-1/3). 네이버·구글 소셜 로그인이 기본, 이메일 로그인은 접어 둔다. */
export default function AuthGate({ next = "/paper" }: { next?: string }) {
  const params = useSearchParams();
  const providers = useSWR<OAuthProvider[]>(["/auth/oauth/providers", null], swrFetcher);
  const loginError = params?.get("login_error");
  const [showEmail, setShowEmail] = useState(false);
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const available = providers.data ?? [];
  const noSocial = providers.data !== undefined && available.length === 0;

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

  const startUrl = (id: string) => `${API_BASE}/auth/oauth/${id}/start?next=${encodeURIComponent(next)}`;

  return (
    <div className="panel auth-card">
      <div>
        <h1 className="auth-title">모의투자를 시작하려면 로그인하세요</h1>
        <p className="auth-sub">처음이면 바로 가입됩니다. 예측과 성적표는 로그인 없이 볼 수 있습니다.</p>
      </div>

      {loginError && (
        <div className="banner" role="alert">
          {LOGIN_ERRORS[loginError] ?? "로그인하지 못했습니다. 다시 시도하세요."}
        </div>
      )}

      <div className="social-buttons">
        {available.map((p) => (
          <a key={p.id} className={`social-btn social-${p.id}`} href={startUrl(p.id)}>
            {p.id === "naver" ? <NaverMark /> : <GoogleMark />}
            <span>{p.id === "naver" ? "네이버로 시작하기" : "Google로 계속하기"}</span>
          </a>
        ))}
        {noSocial && (
          <p className="auth-sub">소셜 로그인 키가 아직 설정되지 않았습니다. 아래 이메일 로그인을 쓰세요.</p>
        )}
      </div>

      {!showEmail && !noSocial ? (
        <button type="button" className="link-btn" onClick={() => setShowEmail(true)}>
          이메일로 로그인
        </button>
      ) : (
        <form onSubmit={submit} className="email-form">
          <span className="seg" role="group" aria-label="로그인 또는 가입">
            <button type="button" aria-pressed={mode === "login"} onClick={() => setMode("login")}>
              로그인
            </button>
            <button type="button" aria-pressed={mode === "signup"} onClick={() => setMode("signup")}>
              가입
            </button>
          </span>
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
            {busy ? "처리 중…" : mode === "login" ? "이메일로 로그인" : "이메일로 가입"}
          </button>
        </form>
      )}
    </div>
  );
}
