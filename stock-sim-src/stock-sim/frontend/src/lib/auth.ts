"use client";

// 로그인 상태 (SPEC 6.8 FR-AU-2).
// - 액세스 토큰(30분)은 메모리에만 둔다. localStorage 에 두지 않아 XSS 로 토큰이 새는 범위를 줄인다.
// - 리프레시 토큰(14일)은 서버가 httpOnly 쿠키로 준다. 새로고침하면 /auth/refresh 로 로그인을 복구한다.
// - 만료 1분 전에 자동으로 다시 받는다.
import { useEffect, useState } from "react";

import { API_BASE, ApiError } from "@/lib/api";

export interface AuthUser {
  id: number;
  email: string | null; // 소셜 로그인은 이메일이 없을 수 있다 (네이버는 인증 여부를 몰라 저장하지 않음)
  display_name: string | null;
  created_at: string;
}

interface Session {
  token: string;
  user: AuthUser;
}

interface TokenResponse {
  access_token: string;
  expires_in: number;
  user: AuthUser;
}

let session: Session | null = null;
let restored = false;
let restoring: Promise<void> | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((f) => f());
}

async function authCall(path: string, body?: unknown): Promise<TokenResponse | null> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/auth${path}`, {
      method: "POST",
      credentials: "include", // 리프레시 쿠키 주고받기
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "서버에 연결할 수 없습니다.");
  }
  const json = await res.json().catch(() => ({}));
  if (!res.ok)
    throw new ApiError(res.status, json?.error?.code ?? "HTTP_ERROR", json?.error?.message ?? res.statusText);
  return (json?.data as TokenResponse) ?? null;
}

// 이 브라우저의 마지막 로그인 상태 (리프레시 쿠키는 httpOnly 라 스크립트가 볼 수 없다).
// "0"(로그아웃했거나 복구에 실패)이면 새로고침 때 /auth/refresh 를 부르지 않는다 (매번 401 이 나던 헛요청).
// 기록이 없으면(처음 보는 브라우저, 이 기능 전에 로그인한 사람) 한 번은 복구를 시도한다.
const HINT = "stocksim.session";
function setHint(on: boolean) {
  try {
    window.localStorage.setItem(HINT, on ? "1" : "0");
  } catch {
    /* 저장소를 못 쓰는 환경: 매번 복구를 시도한다 */
  }
}
function hasHint(): boolean {
  try {
    return window.localStorage.getItem(HINT) !== "0";
  } catch {
    return true;
  }
}

function accept(t: TokenResponse | null) {
  clearTimeout(timer);
  session = t ? { token: t.access_token, user: t.user } : null;
  setHint(!!t);
  if (t) timer = setTimeout(() => void refreshAccess(), Math.max(t.expires_in - 60, 30) * 1000);
  emit();
}

/** 리프레시 쿠키로 새 액세스 토큰. 실패(쿠키 없음/만료/로그아웃됨)면 로그아웃 상태로 둔다. */
export async function refreshAccess(): Promise<string | null> {
  try {
    accept(await authCall("/refresh"));
  } catch (e) {
    if (e instanceof ApiError && e.status === 0) return session?.token ?? null; // 네트워크 오류: 상태 유지
    accept(null);
  }
  return session?.token ?? null;
}

function restore(): Promise<void> {
  if (restored) return Promise.resolve();
  // 소셜 로그인에서 막 돌아왔으면(서버가 ?login=ok 를 붙인다) 쿠키가 새로 생긴 것이므로 반드시 복구한다
  const url = new URL(window.location.href);
  const fromOAuth = url.searchParams.get("login") === "ok";
  if (fromOAuth) {
    url.searchParams.delete("login");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
  if (!fromOAuth && !hasHint()) {
    restored = true;
    emit();
    return Promise.resolve();
  }
  restoring ??= refreshAccess().then(() => {
    restored = true;
    emit();
  });
  return restoring;
}

export async function login(email: string, password: string) {
  accept(await authCall("/login", { email, password }));
}

export async function signup(email: string, password: string) {
  accept(await authCall("/signup", { email, password }));
}

export async function logout() {
  try {
    await authCall("/logout");
  } finally {
    accept(null);
  }
}

/** 토큰이 거부됐을 때(401): 한 번 갱신을 시도하고, 안 되면 로그아웃 상태가 된다. */
export function onUnauthorized() {
  void refreshAccess();
}

/** [token, ready] — ready 전에는 아직 로그인 복구를 시도 중이다 */
export function useToken(): [string | null, boolean] {
  const { token, ready } = useAuth();
  return [token, ready];
}

export function useAuth(): { token: string | null; user: AuthUser | null; ready: boolean } {
  const [, force] = useState(0);
  useEffect(() => {
    const f = () => force((n) => n + 1);
    listeners.add(f);
    void restore();
    return () => {
      listeners.delete(f);
    };
  }, []);
  return { token: session?.token ?? null, user: session?.user ?? null, ready: restored };
}
