#!/usr/bin/env bash
# 서버에서 매일 실행 (cron): DB 전체를 덤프해 ~/backups 에 두고 14일 지난 것은 지운다.
set -euo pipefail
cd ~/stocksim
out=~/backups/stocksim-$(date +%Y%m%d-%H%M).dump
docker compose exec -T db pg_dump -U stock -Fc stocksim > "$out"
find ~/backups -name 'stocksim-*.dump' -mtime +14 -delete
echo "$(date '+%F %T') 백업 $(du -h "$out" | cut -f1) $out"
