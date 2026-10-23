import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import NavLinks from "@/components/ui/NavLinks";
import { ToastProvider } from "@/components/ui/Toast";
import { DISCLAIMER } from "@/types";

import "./globals.css";

export const metadata: Metadata = { title: "StockSim", description: "코스피 대형주 5거래일 예측과 모의투자" };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
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
