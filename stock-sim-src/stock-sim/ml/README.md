# ml/
Colab Pro에서 학습하고, 결과 모델을 `ml/models/{model_version}/`에 둔다 (git 에는 올리지 않음).
로직은 `src/` 에만 있고 노트북과 백엔드는 이를 import 한다 (학습-서빙 피처 코드 공유, SPEC 8.2).

- `src/features.py`      피처 29개, 타깃, 근거 문장, 뉴스 시점 정렬. 백엔드 `app/ml/features.py` 가 그대로 import
- `src/walk_forward.py`  학습 3년/검증 3개월/테스트 3개월, 5거래일 간격, 3개월씩 전진
- `src/train_lgbm.py`    상승/초과 확률 분류기 + q10/q50/q90 분위수 회귀, 확률 보정, 베이스라인 비교, 산출물 저장
- `src/evaluate.py`      방향 정확도, Brier, pinball loss, 범위 적중률, IC, 보정 곡선
- `src/synthetic.py`     합성 데이터 (테스트/파이프라인 검증)
- `src/data.py`          CSV 읽기, 데이터 해시
- `notebooks/02_lgbm_baseline.ipynb`  Colab 학습 노트북

## 흐름
```bash
make export-data   # DB -> data/*.csv  (Drive 의 StockSim/data 에 올린다)
# Colab 에서 02_lgbm_baseline.ipynb 실행 -> 산출물 zip 다운로드 -> ml/models/ 에 풀기
# .env 에 MODEL_VERSION=<버전> 설정 후 API 재시작 (features.json 불일치 시 기동 거부)
make train         # (선택) 로컬에서 같은 학습을 실행
```
