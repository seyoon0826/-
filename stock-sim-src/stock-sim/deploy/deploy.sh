#!/usr/bin/env bash
# 로컬에서 실행: 코드 + 모델 + 서버용 .env 를 서버로 보내고 다시 빌드해 띄운다.
# 사용: deploy/deploy.sh            (deploy/server.conf 의 HOST, KEY 를 쓴다)
#       deploy/deploy.sh --no-up    파일만 보낸다 (처음 배포 때 DB 를 옮기기 전)
# server.conf 예:  HOST=ubuntu@3.35.0.1   KEY=~/.ssh/LightsailDefaultKey-ap-northeast-2.pem
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/server.conf
[ -f deploy/.env.production ] || { echo "deploy/.env.production 이 없습니다: python3 deploy/make_env.py <도메인>"; exit 1; }
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new"

rsync -az --delete -e "$SSH" \
  --exclude .git --exclude .env --exclude .venv --exclude '__pycache__' --exclude .ruff_cache \
  --exclude node_modules --exclude frontend/.next --exclude .taste-skill --exclude data \
  --exclude .kis_token.json --exclude deploy/.env.production --exclude deploy/server.conf \
  ./ "$HOST:~/stocksim/"
rsync -az -e "$SSH" deploy/.env.production "$HOST:~/stocksim/.env"
$SSH "$HOST" 'chmod 600 ~/stocksim/.env'
[ "${1:-}" = "--no-up" ] && { echo "파일만 보냈습니다"; exit 0; }
$SSH "$HOST" 'cd ~/stocksim && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --remove-orphans && docker image prune -f >/dev/null && docker compose -f docker-compose.yml -f docker-compose.prod.yml ps'
