up:
	docker compose up -d db redis
down:
	docker compose down
api:
	cd backend && uvicorn app.main:app --reload
test:
	cd backend && pytest -q
collect:
	python scripts/collect_prices.py
apitest:
	python scripts/test_apis.py
