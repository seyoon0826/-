"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import Logo from "@/components/ui/Logo";
import ThemeToggle from "@/components/ui/ThemeToggle";
import { logout, useAuth } from "@/lib/auth";

const LINKS = [
  { href: "/predict", label: "예측" },
  { href: "/paper", label: "모의투자" },
  { href: "/ranking", label: "랭킹" },
  { href: "/performance", label: "성적표" },
];

export default function NavLinks() {
  const path = usePathname();
  const { user, ready } = useAuth();
  return (
    <nav className="nav" aria-label="주 메뉴">
      <Link href="/" className="brand" aria-label="StockSim 처음으로">
        <Logo />
      </Link>
      {LINKS.map((l) => (
        <Link key={l.href} href={l.href} className={path?.startsWith(l.href) ? "active" : ""}>
          {l.label}
        </Link>
      ))}
      {user?.is_admin && (
        <Link href="/admin" className={path?.startsWith("/admin") ? "active" : ""}>
          관리자
        </Link>
      )}
      <span className="nav-user">
        <ThemeToggle />
        {ready && user ? (
          <>
            <span className="label" title={user.email ?? undefined}>
              {user.display_name ?? user.email ?? "내 계정"}
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
