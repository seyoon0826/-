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

## 소셜 로그인 (네이버, 구글)
키를 `.env` 에 넣으면 로그인 화면에 버튼이 나타난다. 이메일 로그인은 "이메일로 로그인" 아래에 접혀 있다.

네이버 ([developers.naver.com](https://developers.naver.com/apps))
1. Application > 애플리케이션 등록, 사용 API: 네이버 로그인
2. 제공 정보: 별명(필수), 이메일(선택). 이메일 인증 여부를 주지 않아 기존 이메일 계정에 자동 연결하지 않는다.
3. 환경: PC 웹, 서비스 URL `http://localhost:3000`, Callback URL `http://localhost:8000/api/v1/auth/oauth/naver/callback`
4. Client ID/Secret -> `NAVER_LOGIN_CLIENT_ID`, `NAVER_LOGIN_CLIENT_SECRET` (뉴스 검색용 `NAVER_CLIENT_ID` 와 별개)
5. 검수 전에는 '멤버관리'에 등록한 네이버 아이디만 로그인할 수 있다.

구글 ([console.cloud.google.com](https://console.cloud.google.com))
1. API 및 서비스 > OAuth 동의 화면 구성 (외부, 테스트 사용자 추가)
2. 사용자 인증 정보 > OAuth 클라이언트 ID > 웹 애플리케이션
3. 승인된 리디렉션 URI: `http://localhost:8000/api/v1/auth/oauth/google/callback`
4. 클라이언트 ID/보안 비밀 -> `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`

도메인 연결 시 `API_PUBLIC_URL`, `FRONTEND_URL` 을 바꾸고 두 콘솔의 Redirect URI 도 새 주소로 등록한다.
계정 연결: 같은 소셜 계정은 항상 같은 사용자. 제공자가 '인증된 이메일'이라고 알려준 경우에만 같은 이메일의 기존 계정에 연결한다.

## 관리자
- `.env` 의 `ADMIN_EMAILS` (쉼표 구분)에 있는 이메일로 **구글 로그인**한 계정이 관리자다. 비밀번호가 있는 계정은
  이메일이 같아도 관리자가 아니다 (이메일 가입은 이메일 소유를 확인하지 않기 때문).
- 메뉴의 "관리자" (`/admin`): 사용자·모의투자·모델 현황, 사용자 검색과 상세(계좌, 최근 주문),
  이용 정지/해제, 모든 기기 로그아웃, 랭킹 숨김, 수집·예측 작업 기록, 관리 기록.
- 구글 앱이 '테스트 중'이면 관리자 이메일을 구글 콘솔 > 대상 > 테스트 사용자에 넣어야 로그인할 수 있다.

## 화면 모드
상단 오른쪽 버튼으로 라이트/다크를 바꾼다. 처음에는 기기 설정을 따르고, 고른 모드는 브라우저에 기억된다.

## 인증과 AI 계정 (Phase 8)
- 이메일+비밀번호 가입/로그인 (argon2). 액세스 토큰 30분은 프론트 메모리에만, 리프레시 토큰 14일은 httpOnly 쿠키.
  로그아웃하면 그 사용자의 리프레시 토큰이 모두 무효가 된다. 로그인/가입은 IP 당 분당 10회.
- AI 계정: 매 거래일 09:05, 직전 거래일 예측의 상승확률 순위 상위 5종목을 총자산/5 목표로 시장가 매수,
  보유 종목이 10위 밖으로 밀리면 전량 매도 (`AI_MODE=probability` 로 SPEC 원안의 55%/50% 문턱 규칙 사용 가능). 일반 주문과 같은 엔진(검증·수수료·세금)을 거치며 주문에 근거 예측 ID 를 남긴다.

## 모델 학습 (확장 유니버스)
```bash
python scripts/collect_training_universe.py   # 1회: 2018년부터 매달 시총 상위 100 명단 + 그 종목들 일봉 (active=false)
python scripts/backfill_valuation.py          # 1회: PER/PBR/배당수익률 (이후 일일 수집이 함께 받는다)
make export-all && make train-pit              # 학습: 시점별 명단 종목, 평가: 서비스 30종목
```
- 학습 데이터는 그 시점에 시총 상위 100 에 있던 종목만 쓴다(생존 편향 제거). 종목 간 순위 피처와 상대 라벨도 그날 명단 안에서 계산한다.
- 오프라인 성적(metrics.json)은 서비스 종목·서빙과 같은 순위 기준으로 매긴다. 화면의 예측력 안내(low/ranking/ok)가 이 값을 쓴다.
- 학습 후 `.env` 의 `MODEL_VERSION` 을 새 폴더 이름으로 바꾸고 API/스케줄러를 재시작한다.

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
