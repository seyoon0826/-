"use client";

import { setTheme, useTheme } from "@/lib/theme";

/** 라이트/다크 전환. 누를 때마다 바뀌고, 선택은 이 브라우저에 기억된다. */
export default function ThemeToggle() {
  const theme = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  const label = theme === "dark" ? "라이트 모드로" : "다크 모드로";
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => setTheme(next)}
      aria-label={label}
      title={label}
      disabled={theme === null}
    >
      {theme === "dark" ? (
        <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden>
          <circle cx="12" cy="12" r="4.5" fill="currentColor" />
          <g stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" />
          </g>
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden>
          <path fill="currentColor" d="M20.5 14.6A8.5 8.5 0 0 1 9.4 3.5a8.5 8.5 0 1 0 11.1 11.1z" />
        </svg>
      )}
    </button>
  );
}
