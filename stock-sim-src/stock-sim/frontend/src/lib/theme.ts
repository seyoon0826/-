"use client";

import { useEffect, useState } from "react";

// 화면 모드 (라이트/다크). <html data-theme> 에 둔다.
// 저장한 선택이 없으면 기기 설정(prefers-color-scheme)을 따른다. 첫 그림 전에 layout 의 THEME_SCRIPT 가 정한다.
export type Theme = "light" | "dark";
const KEY = "stocksim.theme";
const EVENT = "stocksim-theme";

/** 첫 페인트 전에 실행되는 인라인 스크립트 (깜빡임 방지). 저장소를 못 쓰면 기기 설정을 따른다. */
export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("${KEY}");}catch(e){}if(t!=="light"&&t!=="dark"){t=window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}document.documentElement.dataset.theme=t;})();`;

function stored(): Theme | null {
  try {
    const t = window.localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function setTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  try {
    window.localStorage.setItem(KEY, t);
  } catch {
    /* 저장 못 해도 이번 화면에는 적용된다 */
  }
  window.dispatchEvent(new Event(EVENT));
}

/** 지금 화면 모드. 바뀌면 다시 그린다 (차트처럼 색을 직접 넘기는 컴포넌트용). 서버 렌더에서는 null. */
export function useTheme(): Theme | null {
  const [theme, set] = useState<Theme | null>(null);
  useEffect(() => {
    const sync = () => set(currentTheme());
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystem = () => {
      if (stored() === null) {
        document.documentElement.dataset.theme = systemTheme();
        sync();
      }
    };
    sync();
    window.addEventListener(EVENT, sync);
    media.addEventListener("change", onSystem);
    return () => {
      window.removeEventListener(EVENT, sync);
      media.removeEventListener("change", onSystem);
    };
  }, []);
  return theme;
}

// 차트 라이브러리는 CSS 변수를 직접 못 읽어 계산된 색을 넘긴다.
// 스타일시트가 아직 적용되지 않은 순간(첫 로딩, 개발 중 CSS 교체)에는 빈 값이 오므로 라이트 모드 값을 쓴다.
const FALLBACK: Record<string, string> = {
  "--up": "#d92d20",
  "--dn": "#1d4ed8",
  "--text": "#0a0a0a",
  "--text-2": "#4f5866",
  "--text-3": "#5f6875",
  "--border": "rgba(10, 10, 10, 0.09)",
  "--surface": "#ffffff",
  "--signal": "#0a0a0a",
};

export function cssVar(name: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || FALLBACK[name] || "#888888";
}
