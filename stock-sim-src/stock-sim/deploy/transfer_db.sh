#!/usr/bin/env bash
# 처음 한 번: 로컬 DB(시세, 예측 기록, 계좌)를 서버로 옮긴다. 서버 DB 는 비어 있어야 한다.
# TimescaleDB 는 복원 전후로 pre_restore/post_restore 를 불러야 한다 (양쪽 버전이 같아야 함: compose 에 고정).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/server.conf
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new"
dump=$(mktemp -t stocksim).dump

docker compose exec -T db pg_dump -U stock -Fc stocksim > "$dump"
echo "로컬 덤프 $(du -h "$dump" | cut -f1)"
rsync -az -e "$SSH" "$dump" "$HOST:~/stocksim-initial.dump"
rm -f "$dump"

$SSH "$HOST" bash -s <<'REMOTE'
set -euo pipefail
cd ~/stocksim
C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$C up -d db
until $C exec -T db pg_isready -U stock -d stocksim >/dev/null 2>&1; do sleep 2; done
tables=$($C exec -T db psql -U stock -d stocksim -Atc "select count(*) from information_schema.tables where table_schema='public'")
if [ "$tables" != "0" ]; then echo "서버 DB 가 비어 있지 않습니다 (테이블 $tables 개). 중단"; exit 1; fi
$C exec -T db psql -U stock -d stocksim -c "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT timescaledb_pre_restore();"
$C exec -T db pg_restore -U stock -d stocksim --no-owner < ~/stocksim-initial.dump || true
$C exec -T db psql -U stock -d stocksim -c "SELECT timescaledb_post_restore();"
$C exec -T db psql -U stock -d stocksim -Atc "select 'users', count(*) from users union all select 'prices_daily', count(*) from prices_daily union all select 'predictions', count(*) from predictions"
rm -f ~/stocksim-initial.dump
REMOTE
