import { ImageResponse } from "next/og";

// 링크 미리보기 (카카오톡, 슬랙, X 등). 소개 페이지 첫 화면과 같은 말과 색:
// 하늘 면 위 먹 글씨, 오른쪽 아래 빨강 무대에 순위별 '5거래일 뒤 범위' 막대 다섯 줄.
export const alt = "StockSim: 매일 장 마감 뒤, 30종목을 줄 세웁니다";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const HEADLINE = ["매일 장 마감 뒤,", "30종목을 줄 세웁니다"];
const SUB = "코스피 대형주 5거래일 예측 순위와 AI 모의투자";
// 범위 막대 예시 (아래 끝 %, 위 끝 %): 순위판 모양을 보여주기 위한 그림이지 실제 예측값이 아니다
const ROWS: [number, number][] = [
  [-6, 8],
  [-5, 8],
  [-6, 9],
  [-6, 8],
  [-8, 9],
];

/** 이 이미지에 쓰는 한글만 담은 Noto Sans KR 을 받는다. 실패하면 null (영문만 그린다). */
async function koreanFont(weight: number): Promise<ArrayBuffer | null> {
  const text = encodeURIComponent([...HEADLINE, SUB, "StockSim0123456789"].join(""));
  try {
    const css = await (
      await fetch(`https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@${weight}&text=${text}`)
    ).text();
    const url = css.match(/src: url\((.+?)\) format\('(opentype|truetype)'\)/)?.[1];
    if (!url) return null;
    return await (await fetch(url)).arrayBuffer();
  } catch {
    return null;
  }
}

export default async function OpengraphImage() {
  const [bold, regular] = await Promise.all([koreanFont(800), koreanFont(400)]);
  const fonts = [
    ...(bold ? [{ name: "KR", data: bold, weight: 800 as const, style: "normal" as const }] : []),
    ...(regular ? [{ name: "KR", data: regular, weight: 400 as const, style: "normal" as const }] : []),
  ];
  const scale = 12; // 막대 폭: 1% = 10px
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          position: "relative",
          background: "#d3e4f5",
          color: "#0a0a0a",
          fontFamily: fonts.length ? "KR" : undefined,
        }}
      >
        {/* 왼쪽: 로고와 헤드라인 */}
        <div style={{ display: "flex", flexDirection: "column", padding: "64px 0 0 72px", width: 720 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <div
              style={{
                display: "flex",
                position: "relative",
                width: 56,
                height: 56,
                borderRadius: 14,
                background: "#0a0a0a",
              }}
            >
              <div
                style={{
                  display: "flex",
                  position: "absolute",
                  left: 14,
                  top: 22.75,
                  width: 11.4,
                  height: 10.5,
                  borderRadius: 2.6,
                  background: "#3b6cf0",
                }}
              />
              <div
                style={{
                  display: "flex",
                  position: "absolute",
                  left: 30.6,
                  top: 22.75,
                  width: 15.75,
                  height: 10.5,
                  borderRadius: 2.6,
                  background: "#e5332a",
                }}
              />
              <div
                style={{
                  display: "flex",
                  position: "absolute",
                  left: 26.7,
                  top: 14,
                  width: 2.6,
                  height: 28,
                  borderRadius: 1.3,
                  background: "#fff",
                }}
              />
            </div>
            <div style={{ display: "flex", fontSize: 36, fontWeight: 800, letterSpacing: -1 }}>StockSim</div>
          </div>
          {bold ? (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                marginTop: 96,
                fontSize: 68,
                fontWeight: 800,
                lineHeight: 1.08,
                letterSpacing: -3,
              }}
            >
              {HEADLINE.map((l) => (
                <span key={l} style={{ whiteSpace: "nowrap" }}>
                  {l}
                </span>
              ))}
            </div>
          ) : (
            <div
              style={{
                display: "flex",
                marginTop: 96,
                fontSize: 68,
                fontWeight: 800,
                lineHeight: 1.08,
                letterSpacing: -3,
              }}
            >
              KOSPI 30, ranked every close
            </div>
          )}
          {regular && <div style={{ display: "flex", marginTop: 28, fontSize: 28 }}>{SUB}</div>}
        </div>

        {/* 오른쪽 아래: 빨강 무대 위 흰 순위판 */}
        <div
          style={{
            position: "absolute",
            right: 0,
            bottom: 0,
            width: 440,
            height: 400,
            background: "#e5332a",
            display: "flex",
            alignItems: "flex-end",
            padding: "0 0 0 40px",
          }}
        >
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              width: 400,
              background: "#ffffff",
              borderRadius: "8px 0 0 0",
              padding: "26px 28px 30px",
              gap: 22,
            }}
          >
            {ROWS.map(([lo, hi], i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 18 }}>
                <div style={{ display: "flex", width: 26, fontSize: 26, fontWeight: 800 }}>{i + 1}</div>
                <div style={{ display: "flex", position: "relative", width: 300, height: 18 }}>
                  <div
                    style={{
                      display: "flex",
                      position: "absolute",
                      left: 150 + lo * scale,
                      top: 0,
                      width: -lo * scale,
                      height: 18,
                      background: "#9db7f5",
                    }}
                  />
                  <div
                    style={{
                      display: "flex",
                      position: "absolute",
                      left: 150,
                      top: 0,
                      width: hi * scale,
                      height: 18,
                      background: "#f2a29e",
                    }}
                  />
                  <div
                    style={{
                      display: "flex",
                      position: "absolute",
                      left: 149,
                      top: -6,
                      width: 2,
                      height: 30,
                      background: "#0a0a0a",
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    ),
    { ...size, fonts },
  );
}
