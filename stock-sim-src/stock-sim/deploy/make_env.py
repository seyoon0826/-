"""서버용 .env 만들기: 로컬 .env 의 API 키는 그대로 가져가고, 주소/비밀번호/JWT 키는 운영용으로 새로 만든다.

사용: python3 deploy/make_env.py stocksim.duckdns.org
결과: deploy/.env.production (git 에 올라가지 않는다). 이미 있으면 비밀번호와 JWT 키는 유지한다
(다시 만들면 DB 비밀번호가 바뀌어 기존 서버 DB 에 접속하지 못한다).
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "deploy" / ".env.production"
# 서버에서는 compose 가 정하는 값이라 가져가지 않는다
DROP = {"DATABASE_URL", "REDIS_URL", "MODEL_DIR", "ML_ROOT"}


def parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if m:
            value = re.sub(r"\s+#.*$", "", m.group(2)).strip()  # 줄 끝 주석 제거
            out[m.group(1)] = value
    return out


def main() -> None:
    if len(sys.argv) != 2 or "." not in sys.argv[1] or "/" in sys.argv[1]:
        sys.exit("사용: python3 deploy/make_env.py <도메인>   예: stocksim.duckdns.org")
    domain = sys.argv[1].strip().lower()
    site = f"https://{domain}"
    local = parse(ROOT / ".env")
    previous = parse(OUT)

    env = {k: v for k, v in local.items() if k not in DROP}
    env.update(
        {
            "SITE_DOMAIN": domain,
            "POSTGRES_PASSWORD": previous.get("POSTGRES_PASSWORD") or secrets.token_urlsafe(24),
            "JWT_SECRET": previous.get("JWT_SECRET") or secrets.token_hex(32),
            "REFRESH_COOKIE_SECURE": "true",
            "CORS_ORIGINS": site,
            "API_PUBLIC_URL": site,
            "FRONTEND_URL": site,
            "NEXT_PUBLIC_API_URL": site,
            "NEXT_PUBLIC_SITE_URL": site,
        }
    )
    lines = [
        "# 서버용 환경 파일 — deploy/make_env.py 가 만든다. git 에 올리지 않는다.",
        f"# 소셜 로그인 콜백: {site}/api/v1/auth/oauth/{{google|naver}}/callback",
    ]
    lines += [f"{k}={v}" for k, v in sorted(env.items())]
    OUT.write_text("\n".join(lines) + "\n")
    OUT.chmod(0o600)
    missing = [k for k in ("GOOGLE_CLIENT_ID", "ADMIN_EMAILS", "MODEL_VERSION") if not env.get(k)]
    print(f"만듦: {OUT.relative_to(ROOT)} ({len(env)}개 값, 주소 {site})")
    if missing:
        print("비어 있는 값:", ", ".join(missing))


if __name__ == "__main__":
    main()
