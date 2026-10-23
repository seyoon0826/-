# StockSim
코스피 대형주 5거래일 예측(확률, 범위, 근거, 시나리오) + 모의투자 + 모델 성적표.

## 시작
```bash
cp .env.example .env            # 키 채우기
make up                         # Postgres(Timescale) + Redis
pip install -r backend/requirements.txt
make collect                    # 키 없이 pykrx 데이터 수집 -> data/*.csv
make apitest                    # 키 발급 후 각 API 점검
make api                        # FastAPI (http://localhost:8000/docs)
cd frontend && npm install && npm run dev
```

## 데이터 수집 (Phase 3)
```bash
make migrate     # 테이블 생성
make backfill    # 최초 1회: 캘린더 -> 유니버스(코스피 시총 상위 30, 우선주 제외) -> 2018년부터 일봉/수급/지수
make scheduler   # 상시 실행: 평일 15:40 가격, 16:10 거시, 뉴스 장중 5분/장외 30분, 공시 10분/60분, 일 03:00 주간 갱신
```
- pykrx 는 KRX 회원 로그인이 필요하다 (`.env` 의 `KRX_ID`, `KRX_PW`).
- DART/네이버/ECOS 키가 없으면 해당 작업은 건너뛴다(경고 로그만). 모든 수집은 멱등이라 다시 실행해도 안전하다.
- 장 마감(15:40) 전에는 오늘자 미확정 봉을 수집하지 않는다.
- 실행 이력은 `job_runs` 테이블에서 확인한다.

## 전체 실행 (docker compose)
```bash
cp .env.example .env     # JWT_SECRET 은 반드시 설정: openssl rand -hex 32
docker compose up -d --build
```
- 순서: db/redis -> migrate(alembic upgrade head, 1회) -> api, scheduler, marketdata -> frontend
- 웹: http://localhost:3000 , API 문서: http://localhost:8000/docs
- 모델 산출물은 `ml/models/` 를 읽기 전용으로 마운트한다. `.env` 의 `MODEL_VERSION` 에 폴더 이름을 넣는다.
- 최초 데이터: `docker compose run --rm api python -m app.pipelines.backfill`
- 도메인 연결 시: `CORS_ORIGINS=https://<도메인>`, `REFRESH_COOKIE_SECURE=true`,
  `NEXT_PUBLIC_API_URL=https://<API 주소>` 로 바꾸고 `docker compose up -d --build frontend api`.
  HTTPS 는 앞단 리버스 프록시(Caddy/nginx 등)에서 처리한다.

## 인증과 AI 계정 (Phase 8)
- 이메일+비밀번호 가입/로그인 (argon2). 액세스 토큰 30분은 프론트 메모리에만, 리프레시 토큰 14일은 httpOnly 쿠키.
  로그아웃하면 그 사용자의 리프레시 토큰이 모두 무효가 된다. 로그인/가입은 IP 당 분당 10회.
- AI 계정: 매 거래일 09:05, 직전 거래일 예측으로 상승확률 상위 5종목 중 55% 이상을 총자산/5 목표로 시장가 매수,
  보유 종목이 50% 미만이면 전량 매도. 일반 주문과 같은 엔진(검증·수수료·세금)을 거치며 주문에 근거 예측 ID 를 남긴다.

## CI
`.github/workflows/stocksim-ci.yml`: 백엔드(ruff, black, mypy strict, pytest + 커버리지 70%/엔진 90%),
프론트(eslint, prettier, tsc, build), 도커 이미지 빌드.

## 구조
- `backend/`  FastAPI, 모의투자 엔진, 배치 파이프라인
- `ml/`       Colab 학습 코드, 노트북
- `frontend/` Next.js (예측 / 모의투자 / 성적표)
- `scripts/`  단발성 수집, 점검 스크립트
- `docs/`     스키마, 로드맵

> 본 서비스는 교육·모의투자 목적이며 예측은 투자 권유가 아닙니다.
