# 서버 배포 (주소 하나 + HTTPS)

구조: 서버 한 대에서 docker compose 로 전부 띄운다. 외부에는 Caddy(80/443)만 열리고,
`https://<도메인>/api/*` 는 백엔드, 나머지는 프론트로 간다. 인증서는 Caddy 가 Let's Encrypt 에서 자동으로 받는다.

## 준비 (사람이 하는 것)
1. 서버: Ubuntu 24.04, 메모리 2GB 이상. 고정 IP 를 붙이고 방화벽에서 80, 443 을 연다. SSH 키(.pem)를 `~/.ssh/` 에.
2. 주소: DuckDNS 등에서 `<이름>.duckdns.org` 를 만들고 고정 IP 를 넣는다.
3. `deploy/server.conf.example` 을 `deploy/server.conf` 로 복사해 HOST, KEY 를 채운다.

## 처음 배포
```bash
python3 deploy/make_env.py <이름>.duckdns.org          # deploy/.env.production 생성 (DB 비밀번호, JWT 키 새로 만듦)
ssh -i <키> ubuntu@<IP> 'bash -s' < deploy/server_setup.sh   # Docker, 스왑, 매일 백업 예약
deploy/deploy.sh --no-up                                # 코드, 모델, .env 만 보낸다
deploy/transfer_db.sh                                   # 로컬 DB 를 서버로 (서버 DB 가 비어 있어야 함)
deploy/deploy.sh                                        # 빌드하고 띄운다
```
그다음 소셜 로그인 콘솔에 콜백 주소를 추가한다.
- 구글: 승인된 리디렉션 URI `https://<도메인>/api/v1/auth/oauth/google/callback`
- 네이버: 서비스 URL `https://<도메인>`, Callback URL `https://<도메인>/api/v1/auth/oauth/naver/callback`

## 업데이트
```bash
deploy/deploy.sh
```
코드와 모델(ml/models)을 보내고 바뀐 이미지만 다시 빌드한다. DB 마이그레이션은 migrate 서비스가 자동으로 한다.

## 운영
- 상태: `ssh ... 'cd ~/stocksim && docker compose -f docker-compose.yml -f docker-compose.prod.yml ps'`
- 로그: 같은 곳에서 `... logs -f api` (scheduler, marketdata, caddy 도)
- 백업: 매일 03:30 `~/backups/stocksim-*.dump` (14일 보관). 복원은 `pg_restore` (transfer_db.sh 참고).
- 주소를 바꾸면: `make_env.py <새 도메인>` -> `deploy.sh` -> 소셜 로그인 콘솔 주소 변경.
