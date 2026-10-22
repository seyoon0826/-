FROM python:3.11-slim
# libgomp1: LightGBM 이 쓰는 OpenMP 런타임 (slim 이미지에는 없어 모델 로드가 실패한다)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
