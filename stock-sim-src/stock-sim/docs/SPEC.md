# StockSim 기능 명세서 (SPEC)

- 버전: v1.0 (2026-10-21)
- 대상 독자: 구현 담당(Claude Code), 프로젝트 오너
- 우선순위: **이 문서 > docs/claude-code-prompts.md > 기존 스텁 파일의 주석**. 충돌 시 이 문서를 따르고, 이 문서로 결정할 수 없으면 구현하지 말고 질문한다.
- 표기: `MUST`(필수), `SHOULD`(권장), `MAY`(선택). 요구사항 ID(FR-xxx)는 커밋 메시지와 테스트 이름에 사용한다.

---

## 1. 개요

### 1.1 한 줄 정의
코스피 시총 상위 30종목에 대해 **향후 5거래일 예측(상승확률·예상 범위)과 그 근거·시나리오**를 보여주고, 별도 탭에서 **실시간 시세 기반 모의투자**를 하며, 예측 모델의 **적중 성적을 투명하게 공개**하는 웹 서비스.

### 1.2 목표
1. 예측값만이 아니라 "왜 그렇게 보는지"를 모델이 실제로 사용한 요인(SHAP)과 관련 뉴스·공시로 설명한다.
2. 실제 시장과 같은 규칙(호가 단위, 가격제한폭, 수수료·세금, 부분체결)으로 동작하는 모의투자 엔진을 제공한다.
3. 예측의 과거 성적(베이스라인 대비)을 숨기지 않고 공개한다.
4. 같은 자금으로 AI 계정이 예측대로 매매하게 해서 "나 vs AI" 수익률을 비교한다.

### 1.3 비목표 (v1에서 하지 않음)
- 실제 주문/실계좌 연동 (KIS는 **시세 조회 전용**, 주문 API 호출 금지)
- 크롤링 기반 데이터 수집 (공식 API만 사용)
- 코스닥, 해외주식, ETF, 파생상품
- 시간외 거래, 대체거래소(넥스트레이드) 시간대
- 신용·미수·공매도 (현금 매수와 보유분 매도만)
- 분/틱 단위 예측 (예측은 하루 1회, 장 마감 후)
- 결제일(T+2) 반영 — 매도 대금은 체결 즉시 사용 가능으로 단순화

### 1.4 면책
예측·시나리오가 노출되는 모든 화면과 예측 API 응답 메타에 다음 문구를 포함한다(MUST).
> 본 서비스는 교육·모의투자 목적이며 예측은 투자 권유가 아닙니다.

시나리오 문구에서 "원인", "때문에 올랐다" 같은 인과 단정 표현을 쓰지 않는다. "관련 요인", "과거 유사 사례에서는" 표현을 쓴다(MUST).

---

## 2. 용어

| 용어 | 정의 |
|---|---|
| 기준일(as_of_date) | 예측을 만든 거래일. 해당일 종가까지의 정보만 사용 |
| 호라이즌(horizon) | 예측 기간. v1은 5거래일 고정 |
| 5일 수익률 | `close[t+5] / close[t] - 1` |
| 초과수익 | 5일 수익률 − 같은 기간 KOSPI 수익률 |
| P(up) | 5일 수익률 > 0 일 확률 |
| q10 / q50 / q90 | 5일 수익률의 10/50/90% 분위수 예측 |
| 베이스라인 | 비교용 단순 전략: 항상 상승, 직전 5일 모멘텀, KOSPI 추종 |
| 원장(ledger) | 계좌 현금의 모든 변동 기록. 잔고는 원장으로만 바뀐다 |
| 예약금(reserved_cash) | 미체결 매수 주문에 묶인 현금 |
| 예약수량(reserved_qty) | 미체결 매도 주문에 묶인 보유수량 |
| Provider | 시세 공급원 추상화(pykrx / replay / kis) |

---

## 3. 사용자 시나리오

1. **예측 확인**: 사용자가 `/predict`에서 종목을 고르면, 차트와 함께 "5거래일 상승확률 58%, 예상 범위 −2.1% ~ +4.3%"와 근거 카드 5개, 강세/기본/약세 시나리오, 이 종목의 과거 예측 적중 기록을 본다.
2. **모의 매수**: 예측 화면의 "모의 매수" 버튼 → `/paper`로 이동(종목 선택된 상태) → 호가창 보고 지정가 10주 매수 → 체결 알림 수신.
3. **성과 비교**: `/paper`에서 내 수익률 곡선을 KOSPI, AI 계정과 겹쳐 본다.
4. **모델 신뢰도 확인**: `/performance`에서 모델이 베이스라인보다 얼마나 나은지(또는 못한지) 본다.

---

## 4. 시스템 구조

```
            ┌────────────── 외부 API ───────────────┐
            │ pykrx  KIS(REST/WS)  DART  Naver  ECOS │
            └───┬──────┬───────────┬─────┬──────┬────┘
                │      │           │     │      │
      ┌─────────▼──┐ ┌─▼─────────────────────────────┐
      │ Scheduler  │ │ MarketData Service             │
      │ (배치/수집) │ │ Provider: pykrx|replay|kis     │
      └─────┬──────┘ │ → Redis 최신가 캐시 + pub/sub   │
            │        └──────┬──────────────┬──────────┘
            ▼               │              │
      ┌──────────┐          ▼              ▼
      │PostgreSQL│◄──── Paper Engine   FastAPI(REST/WS)
      │Timescale │      (체결 처리)          │
      └──────────┘                         ▼
            ▲                       Next.js Frontend
      ┌─────┴─────┐
      │ ML 추론    │◄── 모델 파일(ml/models, Colab에서 학습)
      │ + SHAP     │
      │ + LLM 설명 │
      └───────────┘
```

### 4.1 프로세스
| 프로세스 | 역할 | 진입점 |
|---|---|---|
| api | REST/WS 서버 | `uvicorn app.main:app` |
| scheduler | 수집, 일일 배치, 결과 채우기, 스냅샷 | `python -m app.pipelines.scheduler` |
| marketdata | Provider 구동, 시세 Redis 배포, 체결 엔진 트리거 | `python -m app.services.market_data` (scheduler에 합쳐도 됨, 단 1개 인스턴스만) |

`marketdata`는 **반드시 단일 인스턴스**로 실행한다(KIS 웹소켓 세션 중복 방지).

### 4.2 Redis 키/채널
| 키/채널 | 내용 |
|---|---|
| `quote:{code}` (hash) | 최신 체결가, 등락률, 누적거래량, 시각 |
| `orderbook:{code}` (string, JSON) | 매수/매도 1~10호가와 잔량 |
| `ch:quote` (pub/sub) | 시세 이벤트 |
| `ch:orderbook` (pub/sub) | 호가 이벤트 |
| `ch:paper:{account_id}` (pub/sub) | 체결/주문 상태 이벤트 |

---

## 5. 데이터 소스 명세

공통 규칙(MUST)
- 키는 `.env`에서만 읽는다. 로그에 키, 토큰, 전체 요청 헤더를 출력하지 않는다.
- 모든 외부 호출: 타임아웃 15초, 재시도 최대 3회(지수 백오프 1s/2s/4s), 4xx(429 제외)는 재시도하지 않는다.
- 엔드포인트·tr_id·통계코드는 **공식 문서를 확인한 뒤** 구현한다. 확인하지 못한 값은 `# TODO(verify)` 주석과 함께 남기고 보고한다.
- 호출 제한을 넘지 않도록 클라이언트별 레이트 리미터를 둔다(값은 설정).

| ID | 소스 | 용도 | 주기 | 비고 |
|---|---|---|---|---|
| DS-1 | pykrx | 일봉, 거래량, 투자자별 순매수, 시총 순위, KOSPI 지수 | 평일 15:40 KST, 증분 | 키 불필요. 학습 데이터 원천 |
| DS-2 | KIS REST | 현재가, 호가 스냅샷 | 장중 폴링(웹소켓 장애 시 대체) | 모의/실전 도메인 전환 `KIS_PAPER`. 토큰 파일 캐시 |
| DS-3 | KIS WebSocket | 실시간 체결가, 실시간 호가 | 장중 상시 | approval key 발급 → 구독. 종목 구독 수 제한 확인 필요 |
| DS-4 | DART | 공시 목록 | 장중 10분, 장외 60분 | corp_code 매핑은 주 1회 갱신 |
| DS-5 | 네이버 뉴스 검색 | 종목 관련 기사(제목, 요약, 링크, 발행시각) | 장중(08:30~16:00) 5분, 그 외 30분 | 본문 크롤링 금지. 제목+요약만 사용 |
| DS-6 | ECOS | 기준금리, 원/달러 환율 | 평일 1회 | |
| DS-7 | Anthropic API | 시나리오 문장 생성 | 일일 배치 | 예측은 하지 않음 |

### 5.1 Provider (시세 추상화) — FR-MD
| ID | 요구사항 |
|---|---|
| FR-MD-1 | `MarketDataProvider` 인터페이스: `get_quote(code)`, `get_orderbook(code)`, `subscribe(codes, on_quote, on_orderbook)`, `close()` (MUST) |
| FR-MD-2 | `PykrxEodProvider`: 최근 종가를 현재가로 사용. 호가는 종가 ±1틱으로 매수/매도 1호가를 합성, 잔량은 설정값(기본 10,000주) (MUST) |
| FR-MD-3 | `ReplayProvider`: DB 일봉 또는 저장된 틱 파일을 재생. 속도 배율 설정. 테스트에서 결정적(deterministic) 동작 (MUST) |
| FR-MD-4 | `KisProvider`: 웹소켓으로 체결가·호가 구독, 끊기면 지수 백오프 재연결 후 구독 복구, 30초간 메시지 없으면 REST 폴링으로 강등 (MUST) |
| FR-MD-5 | 설정 `MARKET_PROVIDER=pykrx|replay|kis`로 선택. 키 없이 pykrx/replay로 전체 서비스가 동작해야 한다 (MUST) |
| FR-MD-6 | 수신한 시세는 Redis 캐시 갱신 + pub/sub 발행. 체결 엔진과 WS API는 Redis만 구독한다 (MUST) |
| FR-MD-7 | 장중 실시간 틱은 DB에 저장하지 않는다(용량). 1분봉 집계 저장은 MAY |

---

## 6. 기능 요구사항

### 6.1 종목/시세 — FR-ST
| ID | 요구사항 |
|---|---|
| FR-ST-1 | 유니버스: 매월 첫 거래일 기준 코스피 시총 상위 30종목. 편출 종목도 데이터와 과거 예측은 유지, `stocks.active=false` |
| FR-ST-2 | 종목 목록: 현재가, 등락률, 최신 P(up), 예측 기준일 |
| FR-ST-3 | 차트: 일봉 범위 `1M|3M|6M|1Y|3Y`, 장중에는 마지막 봉을 실시간 가격으로 갱신 |
| FR-ST-4 | 차트 마커: 해당 날짜의 뉴스·공시(최대 3건/일, 관련도 순) |

### 6.2 데이터 수집 — FR-CL
| ID | 요구사항 |
|---|---|
| FR-CL-1 | 모든 수집은 멱등이다. 같은 기간을 다시 수집해도 중복 행이 생기지 않는다(upsert, 유니크 제약) |
| FR-CL-2 | 가격: 최초 1회 2018-01-01부터 백필, 이후 증분 |
| FR-CL-3 | 뉴스: 종목명(+별칭)으로 검색, URL 정규화 후 중복 제거, `published_at`은 KST 타임존 포함 저장 |
| FR-CL-4 | 뉴스-종목 매핑: 제목에 종목명/별칭이 있으면 relevance=1.0, 요약에만 있으면 0.6, 검색 결과에만 걸리면 0.3 |
| FR-CL-5 | 수집 실행 기록(`job_runs`: 작업명, 시작/종료, 성공/실패, 건수, 에러)을 남긴다 |
| FR-CL-6 | 일부 종목 실패 시 나머지는 계속 진행하고 실패 목록을 기록한다 |

### 6.3 예측 — FR-PR
| ID | 요구사항 |
|---|---|
| FR-PR-1 | 평일 거래일 15:40 KST 이후 배치로 30종목 예측 생성 (휴장일 스킵) |
| FR-PR-2 | 출력: `p_up`, `q10`, `q50`, `q90`(5일 수익률), `p_outperform`(KOSPI 초과 확률), `model_version` |
| FR-PR-3 | 분위수 단조성 보장: 출력 후 `q10 ≤ q50 ≤ q90`이 되도록 정렬 보정 |
| FR-PR-4 | 예상 가격 범위 표시용: `close × (1+q)`를 호가 단위로 반올림 |
| FR-PR-5 | 근거: P(up) 모델 기준 SHAP 절대값 상위 5개 피처를 `feature, value, shap_value, direction(+/-), rank`로 저장 |
| FR-PR-6 | 피처 이름 → 사람이 읽는 문장 매핑 테이블을 둔다. 예: `ret_20d=0.083` → "최근 20거래일 +8.3% 상승" |
| FR-PR-7 | 관련 뉴스: 기준일 직전 3거래일 내 relevance ≥ 0.6 기사 중 최대 3건 첨부 |
| FR-PR-8 | 과거 유사 사례(MAY, v1.1): 같은 이벤트 유형 뉴스 이후 5일 평균 초과수익과 표본 수 |
| FR-PR-9 | 같은 (종목, 기준일, 호라이즌, 모델버전) 예측은 1건만 존재. 재실행 시 덮어쓰지 않고 스킵 |

### 6.4 시나리오 생성 — FR-SC
| ID | 요구사항 |
|---|---|
| FR-SC-1 | 입력 JSON: 종목명, 기준일 종가, 예측값, 예상 가격 범위, 근거 TOP5(문장), 관련 뉴스(제목·날짜), 시장 상황(KOSPI 5일 수익률, 환율, 기준금리) |
| FR-SC-2 | 출력 JSON: `{"bull": str, "base": str, "bear": str, "summary": str}` 각 2~4문장, 한국어 |
| FR-SC-3 | 시스템 프롬프트에 MUST 포함: 입력에 없는 사실·수치 금지, 인과 단정 금지, 매수/매도 권유 금지, 확률·범위는 입력값 그대로 |
| FR-SC-4 | 사후 검증: 출력에 등장한 숫자(%, 원)가 입력에 존재하는지 검사. 실패 시 1회 재시도, 다시 실패하면 템플릿 문장으로 대체하고 `generated_by=template` 기록 |
| FR-SC-5 | LLM 장애 시에도 예측 저장은 진행한다(시나리오만 템플릿) |
| FR-SC-6 | 모델명, 프롬프트 버전, 토큰 사용량을 저장한다 |

### 6.5 결과 채우기와 성적표 — FR-PF
| ID | 요구사항 |
|---|---|
| FR-PF-1 | 매 거래일 배치에서, 기준일로부터 5거래일이 지난 예측에 `actual_return`, `actual_excess`, `hit_direction`, `in_range(q10~q90 안)`를 채운다 |
| FR-PF-2 | 성적표 지표(모델/베이스라인 각각): 방향 정확도, Brier score, pinball loss(q10/q50/q90), 범위 적중률(목표 80%), IC(일별 순위상관 평균), 표본 수 |
| FR-PF-3 | 기간 필터: 최근 20/60/250거래일, 전체 |
| FR-PF-4 | 보정 곡선: P(up)을 10구간으로 나눠 구간별 실제 상승 비율 |
| FR-PF-5 | 오프라인 검증 결과(walk-forward)와 라이브 결과를 구분 표기한다 |
| FR-PF-6 | 베이스라인보다 나쁜 지표도 그대로 표시한다(숨김 금지) |

### 6.6 모의투자 — FR-PT
상세 규칙은 7장.
| ID | 요구사항 |
|---|---|
| FR-PT-1 | 계좌 생성: 초기 자금 1천만/3천만/5천만/1억 원 중 선택. 사용자당 USER 계좌 1개(v1) |
| FR-PT-2 | 계좌 초기화: 모든 주문 취소, 포지션·원장 보존한 채 새 계좌 생성(이전 계좌는 `closed_at` 기록) |
| FR-PT-3 | 주문: 시장가/지정가 × 매수/매도. 당일 유효(DAY) |
| FR-PT-4 | 주문 취소: PENDING/PARTIAL만 가능, 미체결 수량만 취소 |
| FR-PT-5 | 주문 정정은 v1 미지원(취소 후 재주문) |
| FR-PT-6 | 포트폴리오: 보유종목(수량, 평단, 현재가, 평가금액, 평가손익, 수익률), 현금, 예약금, 총자산, 총수익률 |
| FR-PT-7 | 성과: 일별 총자산 곡선, KOSPI 대비, MDD, 승률(실현 매도 기준), 매매 횟수, 회전율 |
| FR-PT-8 | 실시간: 체결/상태변경 이벤트와 평가금액을 WS로 푸시 |

### 6.7 AI 계정 — FR-AI
| ID | 요구사항 |
|---|---|
| FR-AI-1 | 사용자별로 AI 계정 1개를 사용자 계좌와 같은 초기자금·같은 시작일로 생성 |
| FR-AI-2 | 규칙(설정값 `AI_MODE`): **rank(기본, 2026-10-24 변경)** — 일일 배치 후 같은 날 P(up) 순위 상위 N=5 종목을 동일비중 목표로 매수, 보유 종목이 상위 10위(`AI_SELL_RANK`) 밖이면 전량 매도. **probability(원안)** — P(up) 상위 5 중 P(up) ≥ 0.55 만 매수, P(up) < 0.50 이면 매도. 리밸런싱은 다음 거래일 09:05 시장가. 변경 이유: 정직하게 보정된 P(up) 이 과거 상승 비율(약 43%) 근처에 모여 원안 규칙으로는 매수가 일어나지 않음. 모델의 예측력은 종목 간 순위(IC)에 있음 |
| FR-AI-3 | AI 주문도 일반 주문 API의 서비스 함수와 체결 엔진을 그대로 통과한다(MUST). 수수료·세금 동일 |
| FR-AI-4 | AI 매매 사유(해당 예측 ID)를 주문에 기록 |

### 6.8 인증 — FR-AU
| ID | 요구사항 |
|---|---|
| FR-AU-1 | 이메일+비밀번호 가입/로그인, 비밀번호는 argon2 또는 bcrypt 해시 |
| FR-AU-2 | JWT 액세스 토큰(30분) + 리프레시 토큰(14일, httpOnly 쿠키) |
| FR-AU-3 | 예측/시세/성적표 조회는 비로그인 허용, 모의투자는 로그인 필수 |
| FR-AU-4 | 모의투자 API는 본인 계좌만 접근 가능(타인 account_id 접근 시 404) |
| FR-AU-5 | 로그인 엔드포인트 레이트 리밋(IP당 분당 10회) |
| FR-AU-6 | 소셜 로그인(구글, 네이버). 같은 소셜 계정은 같은 사용자, 제공자가 인증한 이메일만 기존 계정에 연결 |
| FR-AU-7 | 관리자: `ADMIN_EMAILS` 의 이메일로 구글 로그인한, 비밀번호 없는 계정. `/api/v1/admin` 과 `/admin` 화면은 관리자만 |
| FR-AU-8 | 관리자는 사용자 정지/해제(정지 즉시 모든 기기 로그아웃, API 403), 강제 로그아웃, 랭킹 숨김을 할 수 있고 모두 `admin_audit` 에 남는다. 관리자 계정은 정지할 수 없다 |

---

## 7. 모의투자 상세 규칙

### 7.1 장 운영
- 정규장: 09:00~15:30 KST, 거래일만. 휴장일은 `market_calendar` 테이블로 관리(pykrx 거래일 목록으로 생성).
- 장 외 접수 주문은 `PENDING`으로 저장하고 `queued=true`. 다음 거래일 09:00에 처리.
  - 장 외 시장가: 09:00 이후 첫 시세 기준으로 체결.
  - 장 외 지정가: 09:00부터 대기 주문으로 전환.
- 15:30 장 마감 시 미체결 DAY 주문은 `EXPIRED`로 바꾸고 예약금/예약수량을 해제한다.
- `PykrxEodProvider` 사용 시(실시간 없음): 장중 주문은 직전 종가 합성 호가로 즉시 판정. 개발 편의를 위해 `PAPER_IGNORE_MARKET_HOURS=true` 설정을 둔다(기본 false).

### 7.2 주문 검증 (순서대로, 첫 실패에서 REJECTED)
| 순서 | 검사 | 에러 코드 |
|---|---|---|
| 1 | 종목이 유니버스에 존재 | `UNKNOWN_SYMBOL` |
| 2 | 수량 ≥ 1 인 정수, 1회 최대 수량 이하(설정) | `INVALID_QTY` |
| 3 | 지정가: 호가 단위에 맞음 | `INVALID_TICK` |
| 4 | 지정가: 전일 종가 기준 ±30% 이내 | `PRICE_OUT_OF_LIMIT` |
| 5 | 매수: `가용현금 ≥ 필요금액` | `INSUFFICIENT_CASH` |
| 6 | 매도: `보유수량 − 예약수량 ≥ 주문수량` | `INSUFFICIENT_QTY` |

- 가용현금 = `cash − reserved_cash`
- 매수 필요금액: 지정가 = `가격 × 수량 × (1 + 매수수수료율)` 올림. 시장가 = `상한가 × 수량 × (1 + 매수수수료율)` (상한가로 예약 후 체결 시 차액 해제).
- 검증 실패 주문도 `REJECTED` 상태와 `reject_reason`으로 저장한다(사용자에게 내역 표시). API는 422와 에러 코드를 반환한다.

### 7.3 호가 단위 (설정 테이블로 관리, 값은 KRX 현행 규정 확인 후 확정)
| 가격 구간 | 틱 |
|---|---|
| ~ 2,000 미만 | 1 |
| 2,000 ~ 5,000 미만 | 5 |
| 5,000 ~ 20,000 미만 | 10 |
| 20,000 ~ 50,000 미만 | 50 |
| 50,000 ~ 200,000 미만 | 100 |
| 200,000 ~ 500,000 미만 | 500 |
| 500,000 이상 | 1,000 |

`# TODO(verify)`: 구현 전 KRX 현행 호가가격단위를 확인하고 다르면 이 표를 갱신한다.

### 7.4 체결
- **시장가 매수**: 매도 1호가부터 잔량을 소진하며 위로 체결. 10호가까지 소진해도 남으면 남은 수량은 10호가 가격으로 체결(v1 단순화).
- **시장가 매도**: 매수 1호가부터 아래로 동일.
- **지정가 매수**: 호가/체결 이벤트마다 `매도1호가 ≤ 지정가`이면 지정가 이하의 매도 호가들을 낮은 가격부터 잔량만큼 체결. 남으면 `PARTIAL`.
- **지정가 매도**: `매수1호가 ≥ 지정가`이면 대칭으로 처리.
- 체결가는 호가 가격이다(지정가보다 유리할 수 있음).
- 같은 호가 잔량은 **사용자 간에 공유 소진하지 않는다**(각 주문은 독립적으로 호가를 본다, v1 단순화). 단 한 주문 내에서는 잔량을 소진한다.
- 호가 데이터가 없으면(Provider가 호가 미지원) 현재가 1틱 불리한 가격, 잔량 무제한으로 체결한다.
- 체결 1건 = `trades` 1행. 한 주문이 여러 호가에 걸치면 여러 행.

### 7.5 비용
- 매수: `수수료 = floor(체결금액 × buy_fee_rate)`
- 매도: `수수료 = floor(체결금액 × sell_fee_rate)`, `거래세 = floor(체결금액 × sell_tax_rate)`
- 요율은 `fee_config`에서 체결일 기준 유효한 행을 사용. **초기값은 비워두고 오너에게 확인받는다**(세율은 연도별로 바뀜).
- 원 단위 절사(floor). 모든 금액은 정수(원).

### 7.6 상태 머신
```
PENDING ──체결(일부)──► PARTIAL ──체결(잔량)──► FILLED
   │                       │
   ├──취소──► CANCELED ◄───┤ (미체결분만 취소, 체결분 유지)
   ├──장마감──► EXPIRED ◄──┘
   └──검증실패──► REJECTED (생성 시점에만)
```
- 종료 상태: FILLED, CANCELED, EXPIRED, REJECTED. 종료 상태에서 다른 상태로 바뀌지 않는다(MUST, 테스트).

### 7.7 회계
- 매수 체결: `cash −= 체결금액 + 수수료`, `reserved_cash −= 해당 체결분 예약액`(남은 예약은 유지), 포지션 수량 증가, 평단 갱신
  - `새평단 = (기존수량×기존평단 + 체결수량×체결가) / (기존수량+체결수량)`, Decimal로 계산 후 소수 둘째 자리 반올림 저장. 수수료는 평단에 포함하지 않는다.
- 매도 체결: `cash += 체결금액 − 수수료 − 거래세`, 수량 감소, `reserved_qty` 감소, 평단 유지
  - `실현손익 = (체결가 − 평단) × 수량 − 수수료 − 거래세` (매수수수료는 실현손익에 포함하지 않음, v1)
- 수량 0이 된 포지션은 행 삭제가 아니라 qty=0으로 유지(실현손익 보존)
- 평가금액 = Σ(수량 × 현재가), 총자산 = cash + 평가금액 (예약금은 cash 안에 포함되어 있으므로 따로 더하지 않음)
- 총수익률 = `(총자산 − initial_cash) / initial_cash`

### 7.8 원장(cash_ledger)
- 유형: `DEPOSIT`(초기자금), `BUY`, `SELL`, `FEE`, `TAX`, `RESERVE`, `RELEASE`
- `RESERVE`/`RELEASE`는 `reserved_cash`만 바꾸고 `cash`는 바꾸지 않는다. 행에 `affects` 컬럼(`cash`|`reserved`)으로 구분.
- 불변식(MUST, 테스트): 어떤 시점이든 `Σ(cash 영향 원장) == accounts.cash`, `Σ(reserved 영향 원장) == accounts.reserved_cash`, `0 ≤ reserved_cash ≤ cash`.

### 7.9 동시성/정합성
- 주문 생성, 취소, 체결 처리 각각 하나의 트랜잭션. 시작 시 `SELECT ... FROM paper_accounts WHERE id=? FOR UPDATE`.
- 체결 처리는 주문 행도 `FOR UPDATE`로 잠그고 상태가 종료 상태면 즉시 반환(멱등).
- 체결 이벤트 멱등 키: `(order_id, event_seq)` 유니크. 같은 시세 이벤트로 중복 처리되지 않게 한다.
- 주문 생성 API는 `Idempotency-Key` 헤더를 지원(SHOULD). 같은 키 재요청 시 기존 주문 반환.
- 체결 워커는 계좌별 직렬 처리(계좌 ID 해시로 파티션) 또는 DB 락에만 의존. 둘 중 하나를 택해 문서화.

---

## 8. ML 명세

### 8.1 데이터 분할과 누수 방지 (MUST)
- 피처는 기준일 t 종가 시점까지의 정보만 사용.
- 뉴스/공시: `published_at`이 t일 15:30 이전 → t일 피처, 이후 → 다음 거래일 피처.
- 수급(외국인/기관 순매수)은 장 마감 후 확정값이므로 t일 피처로 사용 가능.
- 검증은 walk-forward: 학습 3년 / 검증 3개월 / 테스트 3개월, 3개월씩 전진. 학습 구간 끝과 테스트 시작 사이에 5거래일 간격(embargo).
- 랜덤 분할, 미래 정규화 통계 사용 금지. 스케일링이 필요하면 학습 구간 통계만 사용.

### 8.2 피처 (v1, 가격/수급/시장)
| 그룹 | 피처 |
|---|---|
| 수익률 | ret_1d, ret_5d, ret_20d, ret_60d |
| 추세 | ma5/ma20/ma60 대비 괴리율, ma20 기울기 |
| 모멘텀/과열 | RSI14, 52주 고가 대비 위치 |
| 변동성 | 20일 실현변동성, ATR14/종가 |
| 거래량 | 거래량/20일 평균, 거래대금 순위 |
| 수급 | 외국인·기관 순매수 금액/시총 (1d, 5d, 20d 합) |
| 시장 | KOSPI ret_5d/ret_20d, KOSPI 20일 변동성, 원/달러 5일 변화, 기준금리 |
| 상대 | 종목 ret_20d − KOSPI ret_20d |

v2 추가(뉴스 쌓인 뒤): 최근 1/3일 기사 수, 감성 평균, 이벤트 유형 원-핫, 공시 유형 플래그.

피처 생성 코드는 `ml/src/features.py` 한 곳에 두고 `backend/app/ml/features.py`는 그것을 import 하거나 동일 패키지를 설치해서 쓴다(학습-서빙 불일치 금지).

### 8.3 타깃과 모델
- 분류: `y_up = 1[ret_fwd_5d > 0]`, `y_out = 1[ret_fwd_5d − kospi_fwd_5d > 0]` → LightGBM binary 2개
- 회귀: `ret_fwd_5d` → LightGBM quantile(alpha=0.1, 0.5, 0.9) 3개
- 확률 보정: 검증 구간에서 isotonic 또는 Platt 보정(SHOULD)
- 하이퍼파라미터 탐색은 Colab에서 Optuna(MAY), 탐색 결과 파라미터는 `ml/models/{version}/params.json`

### 8.4 산출물 형식
```
ml/models/{model_version}/
  up_clf.txt, out_clf.txt, q10.txt, q50.txt, q90.txt   # LightGBM booster
  calibrator_up.pkl (선택)
  features.json        # 피처 이름과 순서
  feature_labels.json  # 피처 → 사람이 읽는 문장 템플릿
  metrics.json         # walk-forward 결과(모델+베이스라인)
  meta.json            # 학습 기간, 생성일, 라이브러리 버전, 데이터 해시
```
`model_version` 형식: `lgbm-YYYYMMDD-<짧은해시>`. 서버는 `MODEL_VERSION` 설정으로 로드하고, 시작 시 features.json과 서빙 피처 목록이 다르면 기동 실패(MUST).

### 8.5 Colab 노트북 요구사항
- Drive 마운트 → `data/prices_daily.csv`(또는 DB 덤프) 읽기 → 피처 → walk-forward → 최종 학습 → 산출물 저장 → zip 다운로드.
- 체크포인트/산출물은 Drive에 저장(세션 끊김 대비).
- 노트북은 `ml/src`의 함수를 import 해서 쓰고, 노트북에 로직을 복붙하지 않는다.

### 8.6 뉴스 감성 모델 (v2, 별도 단계)
- 베이스: `klue/roberta-base` 계열, 입력 = 제목 + 요약
- 라벨: 감성(긍정/중립/부정) + 이벤트 유형(실적, 계약·수주, 규제·소송, 지분·경영권, 거시, 기타)
- 데이터: LLM 1차 라벨 1,000~2,000건 + 오너 검수, 약한 라벨(발행 후 1~3일 초과수익 부호) 보조
- 평가: 검수 라벨 홀드아웃 macro-F1
- 서빙: HuggingFace Hub 또는 `ml/models/news-*`에서 로드, CPU 추론

---

## 9. 데이터 모델

모든 시각은 `timestamptz`, 날짜는 `date`(KST 거래일). 금액(원)은 `bigint`, 가격은 `integer`, 비율은 `numeric(12,6)`, 평단은 `numeric(16,2)`.

### 9.1 시장 데이터
**stocks**: `code varchar(6) PK`, `name`, `aliases text[]`, `market`, `sector`, `active bool`, `updated_at`

**prices_daily** (Timescale hypertable on `date`): `code`, `date`, `open`, `high`, `low`, `close`, `volume bigint`, `value bigint`, `foreign_net bigint`, `inst_net bigint`, `market_cap bigint` — PK `(code, date)`

**index_daily**: `index_code`(KOSPI), `date`, `close numeric`, PK `(index_code, date)`

**macro_daily**: `series`(base_rate, usdkrw), `date`, `value numeric`, PK `(series, date)`

**market_calendar**: `date PK`, `is_open bool`

### 9.2 뉴스/공시
**news**: `id bigserial PK`, `url text UNIQUE`(정규화), `title`, `summary`, `source`, `published_at`, `collected_at`

**news_stock_map**: `news_id`, `code`, `relevance numeric(3,2)`, PK `(news_id, code)`

**news_analysis**: `news_id`, `model_version`, `sentiment`, `sentiment_score numeric`, `event_type`, `confidence numeric`, PK `(news_id, model_version)`

**disclosures**: `rcept_no PK`, `code`, `corp_code`, `title`, `filed_at`, `report_type`

### 9.3 예측
**predictions**: `id PK`, `code`, `as_of_date`, `horizon smallint`, `close_at_asof integer`, `p_up`, `p_outperform`, `q10`, `q50`, `q90`, `model_version`, `created_at` — UNIQUE `(code, as_of_date, horizon, model_version)`

**prediction_factors**: `prediction_id`, `rank smallint`, `feature`, `value numeric`, `shap_value numeric`, `label text`(문장) — PK `(prediction_id, rank)`

**prediction_news**: `prediction_id`, `news_id`, PK 둘

**prediction_scenarios**: `prediction_id PK`, `summary`, `bull`, `base`, `bear`, `generated_by`(llm|template), `llm_model`, `prompt_version`, `input_tokens`, `output_tokens`

**prediction_outcomes**: `prediction_id PK`, `target_date`, `actual_return`, `actual_excess`, `hit_direction bool`, `in_range bool`, `filled_at`

**model_metrics**: `model_version`, `scope`(offline|live), `window`(20|60|250|all), `strategy`(model|always_up|momentum|kospi), `metric`, `value`, `n`, `computed_at`

### 9.4 모의투자
**users**: `id PK`, `email UNIQUE`, `password_hash`, `created_at`

**paper_accounts**: `id PK`, `user_id`, `type`(USER|AI), `initial_cash bigint`, `cash bigint`, `reserved_cash bigint`, `created_at`, `closed_at` — CHECK `0 ≤ reserved_cash ≤ cash`

**orders**: `id uuid PK`, `account_id`, `code`, `side`(BUY|SELL), `order_type`(MARKET|LIMIT), `qty int`, `filled_qty int`, `limit_price int null`, `reserved_amount bigint`, `status`, `reject_reason`, `queued bool`, `source`(USER|AI), `prediction_id null`, `idempotency_key null`, `created_at`, `updated_at` — UNIQUE `(account_id, idempotency_key)`, INDEX `(status, code)`

**trades**: `id PK`, `order_id`, `account_id`, `code`, `side`, `qty`, `price`, `amount bigint`, `fee bigint`, `tax bigint`, `event_seq bigint`, `filled_at` — UNIQUE `(order_id, event_seq)`

**positions**: `account_id`, `code`, `qty int`, `reserved_qty int`, `avg_price numeric(16,2)`, `realized_pnl bigint` — PK `(account_id, code)`, CHECK `0 ≤ reserved_qty ≤ qty`

**cash_ledger**: `id PK`, `account_id`, `type`, `affects`(cash|reserved), `amount bigint`(부호 포함), `balance_after bigint`, `ref_type`, `ref_id`, `created_at`

**portfolio_snapshots**: `account_id`, `date`, `cash`, `stock_value`, `total_value`, `return_pct`, `kospi_return_pct` — PK `(account_id, date)`

**fee_config**: `id PK`, `effective_from date`, `buy_fee_rate`, `sell_fee_rate`, `sell_tax_rate`, `note`

### 9.5 운영
**job_runs**: `id PK`, `job`, `started_at`, `finished_at`, `status`, `items_ok`, `items_failed`, `error`

---

## 10. REST API 명세

공통
- Base: `/api/v1` (main.py 라우터 prefix 앞에 추가)
- 응답 envelope: `{"data": ..., "meta": {...}}`, 에러: `{"error": {"code": "INSUFFICIENT_CASH", "message": "..."}}`
- 예측 관련 응답 `meta.disclaimer`에 면책 문구 포함
- 시각은 ISO-8601 KST(+09:00)
- 페이지네이션: `?limit=50&cursor=...`

### 10.1 시세
`GET /stocks` → 종목 목록
```json
{"data":[{"code":"005930","name":"삼성전자","price":72100,"change_pct":1.27,
  "p_up":0.58,"as_of_date":"2026-10-20"}]}
```
`GET /stocks/{code}/quote` → `price, change, change_pct, volume, ts, source(pykrx|kis|replay)`
`GET /stocks/{code}/orderbook` → `asks:[{price,qty}]×10, bids:[...]×10, ts`
`GET /stocks/{code}/chart?range=6M` → `candles:[{date,open,high,low,close,volume}], markers:[{date,type(news|disclosure),title,url}]`

### 10.2 예측
`GET /stocks/{code}/prediction` (최신)
```json
{"data":{
  "code":"005930","as_of_date":"2026-10-20","horizon":5,"close":72100,
  "p_up":0.58,"p_outperform":0.53,
  "range":{"q10":-0.021,"q50":0.009,"q90":0.043,
           "price_low":70600,"price_mid":72700,"price_high":75200},
  "factors":[{"rank":1,"feature":"foreign_net_5d","label":"최근 5일 외국인 순매수 지속",
              "shap":0.031,"direction":"+"}],
  "news":[{"title":"...","url":"...","published_at":"..."}],
  "scenarios":{"summary":"...","bull":"...","base":"...","bear":"...","generated_by":"llm"},
  "model_version":"lgbm-20261015-a1b2c3"},
 "meta":{"disclaimer":"본 서비스는 교육·모의투자 목적이며 예측은 투자 권유가 아닙니다."}}
```
`GET /stocks/{code}/prediction/history?limit=30` → 예측 + outcome(채워졌으면) 목록

`GET /model/performance?window=60&scope=live` → 전략별 지표, 보정 곡선 10구간, 표본 수

### 10.3 인증
`POST /auth/signup`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`

### 10.4 모의투자 (로그인 필수)
| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/paper/accounts` | `{initial_cash}` 계좌 생성(+AI 계정 자동 생성) |
| GET | `/paper/accounts/me` | 내 계좌와 AI 계좌 요약 |
| POST | `/paper/accounts/reset` | 계좌 초기화 |
| POST | `/paper/orders` | `{code, side, order_type, qty, limit_price?}` 헤더 `Idempotency-Key` 선택 |
| DELETE | `/paper/orders/{id}` | 취소 |
| GET | `/paper/orders?status=&code=` | 주문 내역 |
| GET | `/paper/trades?code=` | 체결 내역 |
| GET | `/paper/portfolio` | 보유종목, 현금, 예약금, 총자산 |
| GET | `/paper/performance?account=me|ai` | 일별 곡선, KOSPI 비교, MDD, 승률 |
| GET | `/paper/estimate` | `?code&side&order_type&qty&limit_price` 예상 체결금액·수수료·세금 |

주문 생성 응답: `201` + 주문 객체(시장가가 즉시 체결되면 `status=FILLED`와 trades 포함). 검증 실패: `422` + 에러 코드(7.2).

### 10.5 에러 코드
`UNKNOWN_SYMBOL, INVALID_QTY, INVALID_TICK, PRICE_OUT_OF_LIMIT, INSUFFICIENT_CASH, INSUFFICIENT_QTY, ORDER_NOT_CANCELABLE, ACCOUNT_NOT_FOUND, PREDICTION_NOT_FOUND, UNAUTHORIZED, RATE_LIMITED, UPSTREAM_UNAVAILABLE`

---

## 11. WebSocket 명세

`WS /api/v1/ws/quotes`
- 클라이언트 → `{"op":"subscribe","codes":["005930","000660"]}` / `{"op":"unsubscribe",...}` (연결당 최대 30종목)
- 서버 → `{"type":"quote","code":"005930","price":72100,"change_pct":1.27,"volume":123,"ts":"..."}`
- 서버 → `{"type":"orderbook","code":"005930","asks":[...],"bids":[...],"ts":"..."}`
- 서버 → `{"type":"status","market":"open|closed","provider":"kis|pykrx|replay","degraded":false}`
- 25초마다 ping

`WS /api/v1/ws/paper?token=<access>`
- `{"type":"order_update","order":{...}}`
- `{"type":"trade","trade":{...}}`
- `{"type":"portfolio","total_value":...,"return_pct":...}` (시세 변동 시 최대 1초 1회로 스로틀)

---

## 12. 스케줄 (KST, 거래일만 — market_calendar 기준)

| 시각 | 작업 |
|---|---|
| 08:30 | 시세 Provider 기동/구독, 장 외 대기 주문 확인 |
| 09:00 | 대기(queued) 주문 처리 |
| 09:05 | AI 계정 리밸런싱 주문 |
| 장중 | 뉴스 5분, 공시 10분 |
| 15:30 | 미체결 DAY 주문 EXPIRED, 예약 해제 |
| 15:40 | 가격/수급/지수 수집 → 결과 채우기(FR-PF-1) → 피처 → 예측 → SHAP → 시나리오 → 성적표 지표 재계산 |
| 16:30 | 포트폴리오 스냅샷 (전 계좌) |
| 장외 | 뉴스 30분, 공시 60분 |
| 매주 일 03:00 | DART corp_code 갱신, market_calendar 갱신 |
| 매월 첫 거래일 15:40 | 유니버스 재선정(FR-ST-1) |

배치 실패 시 `job_runs`에 기록하고, 다음 실행 때 누락분을 채운다(catch-up).

---

## 13. 프론트엔드 명세

공통: Next.js App Router, TypeScript strict, 다크/라이트, 모바일 360px 대응. 상승=빨강, 하락=파랑(국내 관례). 숫자는 천단위 콤마, 수익률 소수 둘째 자리. 하단 고정 면책 문구(예측 관련 화면).

### 13.1 `/predict` 종목 목록
- 표: 종목명, 현재가, 등락률, P(up)(막대), 예측 기준일. 정렬(P(up), 등락률), 검색.
- 실시간 가격 반영(WS), 연결 상태 배지.

### 13.2 `/predict/[code]` 예측 상세
1. 헤더: 종목명, 현재가(실시간), 등락
2. 차트: 일봉 캔들 + 거래량, 뉴스/공시 마커(클릭 시 툴팁·링크), 예상 범위(q10~q90)를 미래 5거래일 영역에 음영으로 표시
3. 예측 카드: P(up) 게이지, KOSPI 초과 확률, 예상 가격 범위(하단/중앙/상단)
4. 근거 카드 5개: 문장 + 방향 아이콘 + 기여도 막대
5. 시나리오 탭: 요약 / 강세 / 기본 / 약세. `generated_by=template`이면 작은 표시
6. 관련 뉴스 3건
7. 이 종목 예측 기록: 최근 20건의 예측 범위 vs 실제 결과, 적중률
8. 버튼: "모의 매수" → `/paper?code=005930`

### 13.3 `/paper` 모의투자
- 좌: 종목 선택 + 호가창(10단, 잔량 막대, 클릭 시 가격 입력)
- 중: 주문 패널(매수/매도 토글, 시장가/지정가, 수량, 가격 ±틱 버튼, 가용현금/주문가능수량, `/paper/estimate` 결과로 예상 수수료·세금·총액 표시), 장 상태 표시(장 외면 "다음 장 시작 시 처리" 안내)
- 우: 계좌 요약(총자산, 수익률, 현금, 예약금)
- 하단 탭: 보유종목 / 미체결 / 주문내역 / 체결내역
- 성과 섹션: 수익률 곡선(나 vs KOSPI vs AI), MDD, 승률, 매매 횟수
- 체결 시 토스트 알림
- 계좌 없으면 초기자금 선택 화면

### 13.4 `/performance` 성적표
- 기간/범위(offline|live) 필터
- 지표 표: 모델 vs 베이스라인 3종, 좋은 쪽 강조
- 보정 곡선(대각선 기준선 포함), 범위 적중률(목표 80% 기준선)
- 일별 누적 적중률 추이
- 설명 문단: 각 지표의 의미를 쉬운 말로

### 13.5 상태 처리
로딩 스켈레톤, 에러(재시도 버튼), 빈 상태(예측 없음: "다음 장 마감 후 생성"), WS 끊김 배너와 자동 재연결.

---

## 14. 비기능 요구사항

| 항목 | 요구사항 |
|---|---|
| 성능 | 조회 API p95 < 300ms(로컬), 시세 수신 → 지정가 체결 판정 < 500ms, WS 푸시 지연 < 1s |
| 신뢰성 | Provider 장애 시 서비스는 마지막 가격으로 조회 가능, 상태 `degraded=true` 표시. 주문은 시세가 30초 이상 오래되면 시장가 거부(`UPSTREAM_UNAVAILABLE`) |
| 보안 | 키는 .env, 비밀번호 해시, JWT 서명키 .env, CORS 화이트리스트, SQL은 ORM/바인딩만 |
| 로깅 | 구조화 JSON 로그(요청ID, 계좌ID, 주문ID), 키/토큰 마스킹 |
| 테스트 | 백엔드 커버리지 70% 이상, 모의투자 엔진 90% 이상. 네트워크 없이 `pytest` 전부 통과(외부 API는 mock/replay) |
| 재현성 | 모델 산출물에 데이터 해시와 라이브러리 버전 기록. 같은 데이터·시드로 같은 지표 |
| 코드 품질 | ruff + black(파이썬), eslint + prettier(TS), mypy는 services/paper에 strict |
| 배포 | `docker compose up`으로 db, redis, api, scheduler, marketdata, frontend 전부 기동 |

---

## 15. 설정값 (.env)

| 키 | 기본값 | 설명 |
|---|---|---|
| DATABASE_URL | postgresql+psycopg://stock:stock@localhost:5432/stocksim | |
| REDIS_URL | redis://localhost:6379/0 | |
| MARKET_PROVIDER | pykrx | pykrx / replay / kis |
| REPLAY_SPEED | 60 | replay 배속 |
| KIS_APP_KEY / KIS_APP_SECRET | | |
| KIS_PAPER | True | KIS 모의 도메인 사용(시세 조회만) |
| DART_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, ECOS_API_KEY | | |
| ANTHROPIC_API_KEY | | 없으면 템플릿 시나리오 |
| LLM_MODEL | (오너 지정) | 시나리오 생성 모델 |
| MODEL_VERSION | | 로드할 예측 모델 |
| MODEL_DIR | ../ml/models | |
| JWT_SECRET | | 필수 |
| PAPER_IGNORE_MARKET_HOURS | false | 개발용 |
| PAPER_MAX_ORDER_QTY | 100000 | |
| AI_TOP_N / AI_BUY_THRESHOLD / AI_SELL_THRESHOLD | 5 / 0.55 / 0.50 | |
| NEWS_INTERVAL_OPEN_MIN / NEWS_INTERVAL_CLOSED_MIN | 5 / 30 | |
| UNIVERSE_SIZE | 30 | |

---

## 16. 구현 단계와 완료 기준 (Definition of Done)

각 단계는 **완료 기준을 모두 만족**해야 다음 단계로 간다. 단계 종료 시 보고 형식: 만든 것 / 실행 방법 / 테스트 결과(그대로) / 실행 못 해본 것과 이유 / 남은 TODO(verify).

| Phase | 범위 | 완료 기준 |
|---|---|---|
| 0 | 환경 점검 | `docker compose up db redis` 성공, `/health` 200, 클라이언트 검토 목록 보고 |
| 1 | DB 모델·마이그레이션 | `alembic upgrade head` 성공, 9장 테이블/제약 존재, hypertable 생성, 모델 CRUD 테스트 통과 |
| 2 | 시세 계층 | 3개 Provider, Redis 캐시/pubsub, `/quote` `/orderbook` `/chart` `/ws/quotes` 동작, 네트워크 없는 테스트 통과 |
| 3 | 수집 파이프라인 | pykrx 백필 → prices_daily 적재(오너 PC), 뉴스/공시/ECOS mock 테스트, 멱등 테스트, job_runs 기록 |
| 4 | 모의투자 엔진 | 7장 규칙 전부, 10.4 API, 아래 필수 테스트 전부 통과 |
| 5 | 예측 모델 | ml/src 학습 코드, 합성 데이터로 학습→산출물→서버 로드→추론 end-to-end, 누수 테스트 통과, Colab 노트북 |
| 6 | 일일 배치·예측 API·시나리오 | mock LLM으로 배치 전체 통과, 숫자 검증 실패 시 템플릿 대체 테스트, 10.2 API, 결과 채우기와 성적표 지표 |
| 7 | 프론트엔드 | 13장 화면, `npm run build` 통과, WS 실시간 반영, 모바일 레이아웃 확인 |
| 8 | AI 계정·인증·배포 | FR-AI, FR-AU, 전체 compose 기동, CI(테스트·린트·빌드) |

### 16.1 Phase 4 필수 테스트
1. 평단가: 10주@70,000 + 10주@72,000 → 평단 71,000
2. 매도 실현손익: 수수료·세금 반영 값 일치
3. 시장가 매수가 호가 3단계에 걸쳐 체결 → trades 3행, 평균가 정확
4. 지정가 매수: 대기 → 호가 이벤트로 일부 체결(PARTIAL) → 잔량 체결(FILLED)
5. 취소: PARTIAL 주문 취소 시 체결분 유지, 미체결 예약금만 해제
6. 장 마감 EXPIRED 처리와 예약 해제
7. 잔고 부족/수량 부족/틱 오류/가격제한 각각 REJECTED + 정확한 에러 코드
8. 종료 상태 주문에 체결 이벤트가 와도 변화 없음(멱등)
9. 같은 (order_id, event_seq) 이벤트 2회 → trades 1행
10. **동시성**: 같은 계좌에 1,000만 원 가용, 100만 원짜리 매수 주문 20개 동시 요청 → 정확히 10개만 접수, 나머지 INSUFFICIENT_CASH, 원장 불변식 유지
11. 원장 불변식(7.8)을 모든 테스트 끝에 검사하는 fixture
12. AI 주문이 같은 서비스 함수를 거치는지(특별 경로 없음) 확인

---

## 17. 미결 사항 (구현 전 오너 확인 필요)

| # | 항목 | 기본안 |
|---|---|---|
| Q1 | 수수료율·거래세율 초기값 | 오너가 현행 세율 확인 후 fee_config 시드 입력 |
| Q2 | KRX 현행 호가 단위 | 7.3 표, 확인 후 확정 |
| Q3 | KIS 웹소켓 구독 종목 수 제한, tr_id, 모의 도메인에서 실시간 시세 제공 여부 | 공식 문서 확인. 제한 초과 시 30종목 중 화면에서 보는 종목 우선 구독 |
| Q4 | 시나리오 생성 LLM 모델명과 월 예산 | 배치당 30회 호출, 토큰 기록 |
| Q5 | 과거 뉴스(빅카인즈) 확보 여부 | 미확보 시 v1은 뉴스 피처 없이, 뉴스는 근거 표시용으로만 |
| Q6 | 배포 대상(로컬만 / 클라우드) | v1은 로컬 docker compose |
| Q7 | 사용자 수 | v1은 소수 사용자 가정, 수평 확장 고려 안 함 |
