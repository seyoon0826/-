#!/usr/bin/env bash
# 서버에서 한 번 실행: Docker 설치, 스왑(메모리 2GB 서버에서 프론트 빌드가 죽지 않게), 매일 DB 백업 예약.
# 사용 (로컬에서): ssh -i <키> ubuntu@<IP> 'bash -s' < deploy/server_setup.sh
set -euo pipefail

if ! command -v docker >/dev/null; then
  # Docker 공식 apt 저장소로 설치 (https://docs.docker.com/engine/install/ubuntu/)
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" |
    sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
fi

if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# 서버 시간대: 스케줄러는 코드에서 Asia/Seoul 을 쓰지만, 로그를 읽기 쉽게 맞춘다
sudo timedatectl set-timezone Asia/Seoul || true

mkdir -p ~/stocksim ~/backups
# 매일 03:30 DB 백업 (14일 보관)
( crontab -l 2>/dev/null | grep -v stocksim-backup ; echo "30 3 * * * bash ~/stocksim/deploy/backup.sh >> ~/backups/backup.log 2>&1 # stocksim-backup" ) | crontab -
echo "준비 완료: docker $(docker --version 2>/dev/null | cut -d' ' -f3) / swap $(swapon --show --noheadings | awk '{print $3}')"
