PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
up:
	docker compose up -d db redis
stack:  # 전체(db, redis, migrate, api, scheduler, marketdata, frontend)
	docker compose up -d --build
down:
	docker compose down
fe-check:  # 프론트 lint/format/타입
	cd frontend && npm run lint && npm run format:check && npm run typecheck
migrate:
	cd backend && ../$(PY) -m alembic upgrade head
api:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload
test:
	cd backend && ../$(PY) -m pytest -q
backfill:
	cd backend && ../$(PY) -m app.pipelines.backfill
scheduler:
	cd backend && ../$(PY) -m app.pipelines.scheduler
export-data:
	$(PY) scripts/export_training_data.py
train:
	cd ml && ../$(PY) -m src.train_lgbm --data-dir ../data --out models
collect:
	$(PY) scripts/collect_prices.py
apitest:
	$(PY) scripts/test_apis.py
lint:
	cd backend && ../$(PY) -m ruff check . && ../$(PY) -m black --check .
fmt:
	cd backend && ../$(PY) -m ruff check . --fix && ../$(PY) -m black .
