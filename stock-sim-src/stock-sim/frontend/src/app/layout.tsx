import type { Metadata, Viewport } from "next";
import { Noto_Sans_KR, Schibsted_Grotesk } from "next/font/google";
import type { ReactNode } from "react";

import NavLinks from "@/components/ui/NavLinks";
import { ToastProvider } from "@/components/ui/Toast";
import { THEME_SCRIPT } from "@/lib/theme";
import { DISCLAIMER } from "@/types";

import "./globals.css";

// 글꼴 (taste-skill 시트: Schibsted Grotesk). 한글이 없는 글꼴이라 한글은 굵기가 맞는 Noto Sans KR 이 맡는다.
// 숫자와 영문은 Schibsted, 한글은 Noto: 한 글자 체계에 한 글꼴씩.
const latin = Schibsted_Grotesk({
  weight: ["400", "600", "800"],
  subsets: ["latin"],
  display: "swap",
  variable: "--font-latin",
});
const korean = Noto_Sans_KR({
  weight: ["400", "600", "800"],
  subsets: ["latin"],
  preload: false, // 한글 글리프는 용량이 커서 미리 불러오지 않는다
  display: "swap",
  variable: "--font-kr",
});

const SITE = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: { default: "StockSim", template: "%s | StockSim" },
  description:
    "코스피 대형주 30종목을 매일 장 마감 뒤 5거래일 예측 순위로 줄 세우고, 같은 돈으로 AI와 모의투자를 겨룹니다.",
  applicationName: "StockSim",
  openGraph: { type: "website", siteName: "StockSim", locale: "ko_KR" },
  twitter: { card: "summary_large_image" },
};
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#171a1f" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // data-theme 은 첫 그림 전에 스크립트가 정하므로 서버 HTML 과 다를 수 있다 (suppressHydrationWarning)
    <html lang="ko" className={`${latin.variable} ${korean.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <ToastProvider>
          <header className="topbar">
            <div className="topbar-in">
              <NavLinks />
            </div>
          </header>
          <main className="page">{children}</main>
          <footer className="disclaimer">{DISCLAIMER}</footer>
        </ToastProvider>
      </body>
    </html>
  );
}
