# ml/
Colab Pro에서 학습하고, 결과 모델을 `ml/models/`(또는 HuggingFace Hub)에 저장.

- `src/features.py`        피처 생성 (backend/app/ml/features.py 와 로직 동일하게 유지)
- `src/walk_forward.py`    시간순 분할 검증, 베이스라인 비교
- `src/train_lgbm.py`      분위수 회귀(q10/q50/q90) + 상승확률 분류기
- `src/evaluate.py`        pinball loss, Brier, calibration, IC
- `notebooks/01_eda.ipynb`                  데이터 탐색
- `notebooks/02_lgbm_baseline.ipynb`        베이스라인 학습
- `notebooks/03_news_sentiment_colab.ipynb` KLUE-RoBERTa 파인튜닝 (GPU, 체크포인트는 Drive에 저장)
- `notebooks/04_deep_baseline.ipynb`        PatchTST/TFT 비교군 (선택)
