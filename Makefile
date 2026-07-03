.PHONY: help install test test-unit test-integration test-ci lint fmt api-dev web-dev up down

help:
	@echo "ontoprism — common targets:"
	@echo "  install         PDM install (Python 3.13) + editable local packages"
	@echo "  test            Run the full test suite (pdm run test)"
	@echo "  test-unit       Unit tests only"
	@echo "  test-integration  Integration tests (need live Oxigraph/Postgres)"
	@echo "  test-ci         Tests with coverage (xml + term-missing)"
	@echo "  lint            ruff check + basedpyright"
	@echo "  fmt             ruff format"
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

api-dev:
	pdm run uvicorn backend.main:app --reload --port 8011

web-dev:
	npm --prefix frontend run dev

up:
	docker compose up -d

down:
	docker compose down
