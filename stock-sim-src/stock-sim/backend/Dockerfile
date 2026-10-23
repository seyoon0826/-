# 빌드 컨텍스트: 프로젝트 루트 (docker-compose.yml 의 backend 빌드 설정)
FROM python:3.11-slim
# libgomp1: LightGBM 이 쓰는 OpenMP 런타임 (slim 이미지에는 없어 모델 로드가 실패한다)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ML_ROOT=/ml
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# 학습-서빙 공유 피처 코드 (SPEC 8.2). 모델 산출물(ml/models)은 실행 시 마운트한다.
COPY ml/src /ml/src
COPY backend/ .
RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app
