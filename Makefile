# Algora engine + API. The static site (index.html, algora-*.js/css) needs no build.
PYTHON ?= python3.12
VENV   ?= .venv
BIN    := $(abspath $(VENV))/bin
COMPOSE := docker compose -f infra/docker-compose.yml
# Disposable database for the API tests (it is wiped on every run).
TEST_DATABASE_URL ?= postgresql+psycopg://algora:algora@localhost:5432/algora_test

.PHONY: help setup lint format test test-engine test-api run up down logs migrate schema clean

help:
	@echo "make setup        create $(VENV) and install engine + api with dev tools"
	@echo "make lint         ruff lint + format check"
	@echo "make test         engine tests, then API tests (needs Postgres at TEST_DATABASE_URL)"
	@echo "make run          migrate and run the API locally with reload (needs infra/.env exported)"
	@echo "make up / down    start / stop the docker-compose stack (Postgres+TimescaleDB, Redis, API)"
	@echo "make schema       regenerate engine/schema/strategy.v2.json"

setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e "engine[dev]"
	$(BIN)/pip install -e "api[dev]"

lint:
	cd engine && $(BIN)/ruff check src tests && $(BIN)/ruff format --check src tests
	cd api && $(BIN)/ruff check app tests migrations && $(BIN)/ruff format --check app tests migrations

format:
	cd engine && $(BIN)/ruff format src tests && $(BIN)/ruff check --fix src tests
	cd api && $(BIN)/ruff format app tests migrations && $(BIN)/ruff check --fix app tests migrations

test: test-engine test-api

test-engine:
	cd engine && $(BIN)/pytest --cov --cov-fail-under=90

test-api:
	cd api && TEST_DATABASE_URL=$(TEST_DATABASE_URL) $(BIN)/pytest --cov --cov-fail-under=90

run:
	cd api && $(BIN)/alembic upgrade head && $(BIN)/uvicorn app.asgi:app --reload --port 8000

migrate:
	cd api && $(BIN)/alembic upgrade head

schema:
	cd engine && $(BIN)/python -m algora_engine.schema_export

up:
	$(COMPOSE) up --build -d
	@echo "API on http://127.0.0.1:8000/docs  (dev sign-in codes appear in: make logs)"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api

clean:
	rm -rf $(VENV) engine/.coverage api/.coverage .pytest_cache engine/.pytest_cache api/.pytest_cache
