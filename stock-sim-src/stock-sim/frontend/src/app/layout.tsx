import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { DISCLAIMER } from "@/types";

import "./globals.css";

export const metadata: Metadata = { title: "StockSim", description: "코스피 대형주 5거래일 예측과 모의투자" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <header className="topbar">
          <div className="topbar-in">
            <nav className="nav">
              <Link href="/predict" className="brand">
                StockSim
              </Link>
              <Link href="/predict">예측</Link>
              <Link href="/paper">모의투자</Link>
              <Link href="/performance">성적표</Link>
            </nav>
            <span className="chip chip-accent">디자인 시안 · 모의 데이터</span>
          </div>
        </header>
        <main className="page">{children}</main>
        <footer className="disclaimer">{DISCLAIMER}</footer>
      </body>
    </html>
  );
}
