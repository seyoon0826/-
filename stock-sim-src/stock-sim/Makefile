PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
up:
	docker compose up -d db redis
down:
	docker compose down
migrate:
	cd backend && ../$(PY) -m alembic upgrade head
api:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload
test:
	cd backend && ../$(PY) -m pytest -q
collect:
	$(PY) scripts/collect_prices.py
apitest:
	$(PY) scripts/test_apis.py
