"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { logout, useAuth } from "@/lib/auth";

const LINKS = [
  { href: "/predict", label: "예측" },
  { href: "/paper", label: "모의투자" },
  { href: "/performance", label: "성적표" },
];

export default function NavLinks() {
  const path = usePathname();
  const { user, ready } = useAuth();
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
      <span className="nav-user">
        {ready && user ? (
          <>
            <span className="label" title={user.email}>
              {user.email}
            </span>
            <button className="btn btn-sm" type="button" onClick={() => void logout()}>
              로그아웃
            </button>
          </>
        ) : ready ? (
          <Link href="/paper">로그인</Link>
        ) : null}
      </span>
    </nav>
  );
}
