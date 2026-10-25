// 차트 라이브러리는 CSS 변수를 직접 못 읽어 계산된 색을 넘긴다.
// 스타일시트가 아직 적용되지 않은 순간(첫 로딩, 개발 중 CSS 교체)에는 빈 값이 오므로 기본값을 쓴다.
const FALLBACK: Record<string, string> = {
  "--up": "#f2555c",
  "--dn": "#5b95ff",
  "--text": "#eef2f8",
  "--text-2": "#a3aec0",
  "--text-3": "#8592a8",
  "--border": "rgba(163, 174, 192, 0.14)",
  "--surface": "#0b1424",
  "--signal": "#f2b84b",
};

export function cssVar(name: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || FALLBACK[name] || "#888888";
}
