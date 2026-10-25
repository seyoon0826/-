// StockSim 로고. 심볼은 예측 화면의 '5거래일 뒤 범위 막대'를 줄인 것:
// 가운데 세로선이 0%, 왼쪽 파랑은 내려갈 수 있는 폭, 오른쪽 빨강은 오를 수 있는 폭 (한국 시장 색).
// 판은 글자색(currentColor)을 따르므로 라이트/다크 어디서나 보이고, 0% 선은 바탕색으로 뚫린다.
export const MARK = {
  view: 32,
  tile: { r: 8 },
  dn: { x: 8, y: 13, w: 6.5, h: 6 },
  up: { x: 17.5, y: 13, w: 9, h: 6 },
  zero: { x: 15.25, y: 8, w: 1.5, h: 16 },
};

export function LogoMark({ size = 22, title }: { size?: number; title?: string }) {
  const m = MARK;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${m.view} ${m.view}`}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      className="logo-mark"
    >
      <rect width={m.view} height={m.view} rx={m.tile.r} fill="currentColor" />
      <rect x={m.dn.x} y={m.dn.y} width={m.dn.w} height={m.dn.h} rx={1.5} fill="var(--logo-dn, #3b6cf0)" />
      <rect x={m.up.x} y={m.up.y} width={m.up.w} height={m.up.h} rx={1.5} fill="var(--logo-up, #e5332a)" />
      <rect x={m.zero.x} y={m.zero.y} width={m.zero.w} height={m.zero.h} rx={0.75} fill="var(--logo-zero, #fff)" />
    </svg>
  );
}

/** 심볼 + 글자 (메뉴 왼쪽) */
export default function Logo() {
  return (
    <span className="logo">
      <LogoMark />
      <span className="logo-word">StockSim</span>
    </span>
  );
}
