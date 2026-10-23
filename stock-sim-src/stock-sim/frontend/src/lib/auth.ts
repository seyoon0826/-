"use client";

// 로그인 토큰 보관. 가입/로그인 API 는 Phase 8 — 그 전에는 개발용으로 토큰을 직접 넣어 쓴다.
import { useEffect, useState } from "react";

const KEY = "stocksim.token";

function read(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(KEY, token);
    else window.localStorage.removeItem(KEY);
    window.dispatchEvent(new Event("stocksim-token"));
  } catch {
    /* 저장소를 못 쓰는 환경: 이번 화면에서만 유지 */
  }
}

/** [token, ready] — ready 전에는 아직 저장소를 읽지 않은 상태(SSR) */
export function useToken(): [string | null, boolean] {
  const [token, setTok] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const sync = () => setTok(read());
    sync();
    setReady(true);
    window.addEventListener("storage", sync);
    window.addEventListener("stocksim-token", sync);
    return () => {
      window.removeEventListener("storage", sync);
      window.removeEventListener("stocksim-token", sync);
    };
  }, []);
  return [token, ready];
}
