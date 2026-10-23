"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/predict", label: "예측" },
  { href: "/paper", label: "모의투자" },
  { href: "/performance", label: "성적표" },
];

export default function NavLinks() {
  const path = usePathname();
  return (
    <nav className="nav" aria-label="주 메뉴">
      <Link href="/predict" className="brand">
        StockSim
      </Link>
      {LINKS.map((l) => (
        <Link key={l.href} href={l.href} className={path?.startsWith(l.href) ? "active" : ""}>
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
