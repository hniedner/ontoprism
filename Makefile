.PHONY: help install test test-unit test-integration test-ci lint fmt clean-workspace api-dev web-dev up down

help:
	@echo "ontoprism — common targets:"
	@echo "  install         PDM install (Python 3.14.x) + editable local packages"
	@echo "  test            Run the full test suite (pdm run test)"
	@echo "  test-unit       Unit tests only"
	@echo "  test-integration  Integration tests (owned disposable services)"
	@echo "  test-ci         Tests with coverage (xml + term-missing)"
	@echo "  lint            ruff check + basedpyright"
	@echo "  fmt             ruff format"
	@echo "  clean-workspace Remove verified test containers, QLever dirs, and coverage shards"
	@echo "  api-dev         Run the FastAPI backend (uvicorn, reload)"
	@echo "  web-dev         Run the SvelteKit frontend dev server"
	@echo "  up / down       docker compose data services (fresh-machine recipe)"

install:
	pdm install --dev

test:
	pdm run test

test-unit:
	pdm run test-unit

test-integration:
	pdm run test-integration

test-ci:
	pdm run test-ci

lint:
	pdm run lint

fmt:
	pdm run fmt

clean-workspace:
	pdm run python -m scripts.dev.cleanup_workspace

api-dev:
	pdm run uvicorn backend.main:app --reload --port 8011

web-dev:
	npm --prefix frontend run dev

up:
	docker compose up -d

down:
	docker compose down
