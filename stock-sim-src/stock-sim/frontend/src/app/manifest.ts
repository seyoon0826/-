import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "StockSim",
    short_name: "StockSim",
    description: "코스피 대형주 30종목 5거래일 예측 순위와 AI 모의투자",
    start_url: "/",
    display: "standalone",
    background_color: "#f2f4f7",
    theme_color: "#0a0a0a",
    lang: "ko",
    icons: [
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml" },
      { src: "/apple-icon", sizes: "180x180", type: "image/png" },
    ],
  };
}
