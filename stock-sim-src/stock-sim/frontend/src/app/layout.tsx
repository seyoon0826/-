import type { ReactNode } from "react";

export const metadata = { title: "StockSim" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <nav>
          <a href="/predict">예측</a> · <a href="/paper">모의투자</a> · <a href="/performance">성적표</a>
        </nav>
        {children}
        <footer>본 서비스는 교육·모의투자 목적이며 예측은 투자 권유가 아닙니다.</footer>
      </body>
    </html>
  );
}
