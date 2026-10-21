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

## 구조
- `backend/`  FastAPI, 모의투자 엔진, 배치 파이프라인
- `ml/`       Colab 학습 코드, 노트북
- `frontend/` Next.js (예측 / 모의투자 / 성적표)
- `scripts/`  단발성 수집, 점검 스크립트
- `docs/`     스키마, 로드맵

> 본 서비스는 교육·모의투자 목적이며 예측은 투자 권유가 아닙니다.
