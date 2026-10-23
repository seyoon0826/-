// 차트 라이브러리는 CSS 변수를 직접 못 읽어 계산된 색을 넘긴다.
// 스타일시트가 아직 적용되지 않은 순간(첫 로딩, 개발 중 CSS 교체)에는 빈 값이 오므로 기본값을 쓴다.
const FALLBACK: Record<string, string> = {
  "--up": "#e24b4a",
  "--dn": "#378add",
  "--text": "#1a1a18",
  "--text-2": "#5f5e5a",
  "--text-3": "#8a8983",
  "--border": "rgba(0, 0, 0, 0.1)",
  "--surface": "#ffffff",
};

export function cssVar(name: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || FALLBACK[name] || "#888888";
}
