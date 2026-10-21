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

## 구조
- `backend/`  FastAPI, 모의투자 엔진, 배치 파이프라인
- `ml/`       Colab 학습 코드, 노트북
- `frontend/` Next.js (예측 / 모의투자 / 성적표)
- `scripts/`  단발성 수집, 점검 스크립트
- `docs/`     스키마, 로드맵

> 본 서비스는 교육·모의투자 목적이며 예측은 투자 권유가 아닙니다.
