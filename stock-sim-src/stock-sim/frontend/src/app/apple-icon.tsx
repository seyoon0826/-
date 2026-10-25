import { ImageResponse } from "next/og";

// 휴대폰 홈 화면 아이콘 (180px). iOS 가 모서리를 둥글게 깎으므로 판은 꽉 채운다.
export const size = { width: 180, height: 180 };
export const contentType = "image/png";

const u = 180 / 32; // 로고 격자 한 칸

export default function AppleIcon() {
  const bar = (x: number, y: number, w: number, h: number, color: string, r = 1.5) => (
    <div
      style={{
        position: "absolute",
        left: x * u,
        top: y * u,
        width: w * u,
        height: h * u,
        borderRadius: r * u,
        background: color,
      }}
    />
  );
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", position: "relative", background: "#0a0a0a" }}>
        {bar(8, 13, 6.5, 6, "#3b6cf0")}
        {bar(17.5, 13, 9, 6, "#e5332a")}
        {bar(15.25, 8, 1.5, 16, "#ffffff", 0.75)}
      </div>
    ),
    size,
  );
}
