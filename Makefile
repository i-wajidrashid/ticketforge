.DEFAULT_GOAL := help
SHELL := /bin/bash

help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## create the venv and install dev dependencies
	uv venv --python 3.12
	uv pip install -e ".[dev]"

up: ## build and start the whole stack
	docker compose up -d --build

down: ## stop the stack (keeps data)
	docker compose down

reset: ## stop and delete all data volumes
	docker compose down -v

logs: ## tail application logs
	docker compose logs -f web api realtime worker

seed: ## seed demo events, customers and orders
	docker compose exec web python manage.py seed_demo_data --orders 400

reindex: ## rebuild the Meilisearch index
	docker compose exec web python manage.py reindex_events

migrate: ## apply Django + Alembic migrations
	docker compose run --rm migrate

test: ## run the test suite
	uv run pytest

coverage: ## run tests with coverage (fails under 85%)
	uv run pytest --cov=api --cov=web --cov=worker --cov=common --cov-report=term-missing

lint: ## ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt: ## auto-format
	uv run ruff check . --fix
	uv run ruff format .

typecheck: ## mypy
	uv run mypy .

check: lint typecheck coverage ## everything CI runs

loadtest: ## Locust UI at http://localhost:8089
	uv run locust -f locustfile.py --host https://localhost

shell: ## Django shell inside the container
	docker compose exec web python manage.py shell
