# Claude Code 단계별 프롬프트

사용법: 프로젝트 루트에서 Claude Code를 실행하면 `CLAUDE.md`를 자동으로 읽는다.
각 Phase는 `docs/SPEC.md`의 해당 장과 16장 완료 기준을 따른다. 프롬프트 앞에 "docs/SPEC.md 기준으로"를 붙여도 된다.
아래 프롬프트를 **Phase 순서대로 하나씩** 붙여넣는다. 한 Phase가 끝나고 직접 확인한 뒤 다음으로 넘어간다.

---

## Phase 0. 환경 점검 (먼저)

```
CLAUDE.md와 프로젝트 구조를 읽고, 현재 상태를 점검해줘.
1. backend/app/clients/ 의 5개 클라이언트를 읽고 문제나 공식 문서와 다를 수 있는 부분을 목록으로 알려줘 (특히 KIS tr_id, ECOS 통계 코드).
2. docker compose로 db/redis를 띄우고, backend가 /health에 응답하는지 확인해줘.
3. 빠진 의존성이나 설정 오류가 있으면 고쳐줘.
코드는 이 단계에서 새로 만들지 말고 점검과 수정만 해줘.
```

## Phase 1. DB 모델과 마이그레이션

```
docs/schema.md와 CLAUDE.md 규칙에 맞춰 SQLAlchemy 모델과 Alembic 마이그레이션을 작성해줘.

테이블:
- stocks(code, name, market, sector), prices_daily(code, date, open, high, low, close, volume, foreign_net, inst_net) — TimescaleDB hypertable 적용
- news, news_stock_map, news_analysis(sentiment, event_type, confidence, model_version)
- predictions(as_of_date, horizon, p_up, q10, q50, q90, model_version), prediction_factors, prediction_scenarios(bull/base/bear), prediction_outcomes(actual_return, hit_direction, in_range)
- paper_accounts(type USER/AI, initial_cash, cash, reserved_cash), orders(status PENDING/PARTIAL/FILLED/CANCELED/REJECTED, filled_qty, reject_reason), trades(fee, tax), positions(avg_price, realized_pnl), cash_ledger(type, amount, balance_after, ref_id), portfolio_snapshots, fee_config(effective_from)
- users (간단한 인증용 최소 필드)

요구사항: 금액 컬럼은 정수 또는 Numeric, 적절한 인덱스/유니크 제약(예: prices_daily (code,date), predictions (code,as_of_date,horizon,model_version)), fee_config 초기 시드는 값 확인이 필요하니 TODO로 두고 나에게 질문해줘.
끝나면 마이그레이션 적용 방법과 간단한 모델 테스트를 같이 줘.
```

## Phase 2. 시세 데이터 계층 (크롤링 아님)

```
MarketDataProvider 추상화와 구현체를 만들어줘 (backend/app/services/market_data.py 및 필요하면 하위 모듈).

인터페이스:
- get_quote(code) -> 현재가, 등락률, 거래량, 시각
- get_orderbook(code) -> 매수/매도 1~10호가와 잔량
- subscribe(codes, callback) -> 실시간 체결가/호가 스트림

구현체:
1. PykrxEodProvider: 종가 기반 (키 없이 동작). 호가는 종가 ± 1틱으로 합성.
2. ReplayProvider: DB의 일봉(또는 저장 틱)을 재생. 테스트/백테스트용.
3. KisProvider: 기존 kis_client.py의 REST 현재가/호가 + 웹소켓 구독.
   - 웹소켓 approval key 발급, 실시간 체결가(H0STCNT0)와 호가(H0STASP0) 구독, 재연결과 구독 복구, 구독 종목 수 제한 처리
   - KIS 모의/실전 도메인 전환 (KIS_PAPER)
   - 스펙은 공식 문서를 확인하고 구현. 확인 못 한 부분은 TODO로 표시.

시세는 Redis에 최신값을 캐시하고 pub/sub으로 배포해서, 모의투자 엔진과 WS API가 같은 소스를 쓰게 해줘.
설정(`MARKET_PROVIDER=pykrx|replay|kis`)으로 구현체를 고르게 하고, 키 없이도 replay/pykrx로 전체가 돌아가게 해줘.
GET /stocks/{code}/quote, GET /stocks/{code}/chart, WS /ws/quotes 를 연결해줘.
Provider 단위 테스트(실제 네트워크 없이)를 포함해줘.
```

## Phase 3. 데이터 수집 파이프라인

```
backend/app/pipelines/ 의 수집 작업을 구현해줘.
- collect_prices: pykrx로 시총 상위 30종목 일봉+외국인/기관 순매수를 증분 수집해 prices_daily에 upsert
- collect_news: 네이버 뉴스 검색 API로 종목별 최신 뉴스를 수집, 중복 제거(body_hash/URL), 종목 매핑(news_stock_map), 발행 시각 저장 (학습 데이터가 되므로 누락 없이)
- collect_disclosures: DART 공시 수집
- 거시 지표: ECOS 기준금리/환율 수집 테이블 추가
- scheduler.py에 스케줄 등록: 가격은 평일 15:40 KST, 뉴스는 장중 5분 / 장외 30분 간격
모두 멱등(재실행해도 중복 없음)이어야 하고, API 실패 시 재시도/로그를 남겨줘. 키 없이 돌릴 수 없는 부분은 mock 응답으로 단위 테스트를 작성해줘.
```

## Phase 4. 모의투자 엔진 (가장 중요)

```
backend/app/services/paper/ 의 모의투자 엔진을 구현해줘. CLAUDE.md의 규칙을 모두 지켜줘.

기능:
- 계좌 생성(초기 자금 1천만~1억), USER/AI 타입
- 주문: 시장가/지정가, 매수/매도. 검증(잔고, 보유수량, 호가 단위, 가격제한폭 ±30%, 장 시간 09:00~15:30, 휴장일). 장 외 주문은 예약 처리.
- 주문 상태 전이: PENDING → PARTIAL → FILLED / CANCELED / REJECTED
- 지정가 매수 시 현금 예약(RESERVE), 체결 시 실제 차감, 취소 시 해제(RELEASE)
- 체결: 시장가는 매수=매도1호가/매도=매수1호가. 주문 수량이 잔량보다 크면 여러 호가에 걸쳐 체결(슬리피지). 지정가는 시세/호가 수신 시 조건 검사 후 체결(MarketDataProvider.subscribe 사용).
- 비용: 수수료와 매도세를 fee_config에서 읽어 반영
- 평단가 갱신, 실현손익, 평가손익, 총자산, 수익률 계산
- cash_ledger 원장 기록, 체결 한 건을 하나의 트랜잭션으로 처리, 계좌 행 잠금, 주문 ID 기준 멱등
- 매일 포트폴리오 스냅샷(KOSPI 대비 수익률 포함)

API: POST /paper/accounts, POST /paper/orders, DELETE /paper/orders/{id}, GET /paper/orders, GET /paper/trades, GET /paper/portfolio, GET /paper/performance(수익률 곡선, MDD, 승률), WS /ws/paper(체결 알림, 평가금액)

테스트(필수): 평단가/손익 계산, 비용 계산, 상태 전이, 잔고 부족 거부, 부분체결, 지정가 대기 후 체결, 동시 주문 100개에도 잔고와 예약금이 어긋나지 않음, 같은 체결 이벤트 중복 처리 시 멱등.
테스트는 ReplayProvider로 네트워크 없이 돌아가야 해. 끝나면 테스트 결과를 그대로 보고해줘.
```

## Phase 5. 예측 모델 (코드는 Colab에서 학습할 수 있게)

```
ml/src/ 에 학습 코드를, backend/app/ml/ 에 서빙 코드를 만들어줘. 피처 생성 로직은 한 모듈을 공유해서 학습/서빙이 어긋나지 않게 해줘.

- 피처: 1/5/20일 수익률, 이평선 괴리, RSI, 변동성, 거래량 급증, 외국인/기관 순매수, KOSPI/환율/금리 시장 피처
- 타깃: 향후 5거래일 수익률 (KOSPI 대비 초과수익도 함께)
- 모델: LightGBM 분위수 회귀(q10, q50, q90) + 상승확률 분류기
- 검증: walk-forward (랜덤 분할 금지), 베이스라인(항상 상승, 5일 모멘텀, KOSPI 추종)과 비교
- 지표: 방향 정확도, pinball loss, Brier, calibration curve, IC. 결과를 JSON으로 저장해 성적표 API가 읽게
- 설명: SHAP TOP5 기여도를 prediction_factors 형식으로 변환
- ml/notebooks/02_lgbm_baseline.ipynb: Colab에서 Drive의 CSV/DB 덤프를 읽어 학습하고 모델 파일을 저장하는 셀 구성
- backend/tests/test_walk_forward_leak.py: 미래 정보 누수가 없음을 검증하는 테스트

내 PC에 학습 데이터가 없으니, 합성 데이터로 학습/서빙 파이프라인이 끝까지 도는 것까지 검증해줘.
```

## Phase 6. 일일 배치와 예측 API, 시나리오

```
daily_batch를 구현해줘 (평일 15:40 KST): 가격/수급 수집 → 피처 생성 → 모델 추론 → SHAP → 시나리오 생성 → predictions/prediction_factors/prediction_scenarios 저장.
- scenario_service: Anthropic API로 강세/기본/약세 시나리오를 생성. 입력은 JSON(예측값, SHAP TOP5, 대표 뉴스 3건, 시장 상황), "입력에 없는 사실 금지" 명시. 출력 후 숫자/종목명이 입력과 일치하는지 간단히 검증하고, 실패 시 재시도 또는 템플릿 문장으로 대체.
- fill_outcomes: 5거래일이 지난 예측에 실제 수익률, 방향 적중, 범위 내 여부를 채움
- API: GET /stocks/{code}/prediction, GET /stocks/{code}/prediction/history, GET /model/performance
- 배치는 일부 종목이 실패해도 나머지를 계속 처리하고, 실패 내역을 기록
키가 없는 환경에서도 mock LLM으로 배치 전체가 도는 테스트를 만들어줘.
```

## Phase 7. 프론트엔드

```
frontend/ 를 완성해줘 (Next.js App Router, TypeScript, Lightweight Charts). 디자인은 깔끔한 금융 대시보드 톤으로.

- /predict : 30종목 목록(현재가, 등락률, 상승확률)
- /predict/[code] : 가격 차트(과거+실시간), 차트 위 뉴스 마커, 예측 카드(상승확률, 예상 범위), 근거 카드(SHAP TOP5를 사람이 읽는 문장으로), 강세/기본/약세 시나리오, 과거 예측 적중 기록, "이 종목 모의 매수" 버튼
- /paper : 호가창 + 주문 패널(예상 수수료/총액 표시), 보유 종목(평가손익 색상), 주문/체결 내역(상태 필터), 수익률 곡선(나 vs KOSPI vs AI), MDD/승률
- /performance : 모델 성적표 (베이스라인 비교, 보정 곡선, 적중률)
- 모든 예측 화면 하단에 면책 문구
- WS로 실시간 시세/체결 알림 반영, 연결 끊김 시 재연결과 상태 표시
- 로딩/에러/빈 상태 처리, 모바일 대응
API 타입은 backend 스키마에서 가져오거나 types/에 정의해서 맞춰줘. 끝나면 npm run build가 통과하는지 확인해줘.
```

## Phase 8. AI 계정과 마무리

```
1. ai_trader: 예측 배치 후 P(up) 상위 5종목을 동일 비중으로 매수하고, 확률이 기준 아래로 내려가면 매도하는 규칙. 일반 주문 API/체결 엔진을 그대로 통과. 규칙은 설정으로 분리.
2. "나 vs AI" 수익률 비교를 /paper/performance와 프론트에 연결.
3. docker-compose로 전체(db, redis, backend, scheduler, frontend)가 한 번에 뜨게 정리하고 README를 갱신.
4. GitHub Actions: 테스트와 린트, 프론트 빌드.
5. 전체 테스트를 돌리고 결과와, 아직 실제 키로 검증하지 못한 부분을 목록으로 정리해줘.
```
