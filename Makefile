.PHONY: help install dev check lint format typecheck security test test-integration eval traffic run run-frontend up down deploy-dev clean

ENV ?= dev

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-18s %s\n", $$1, $$2}'

# ─── Setup ───────────────────────────────────────────────────────────────────

install: ## Install the API package
	pip install -e .

dev: ## Install everything for development and the git hooks
	pip install -e ".[dev,frontend,notebooks]"
	pre-commit install

# ─── Quality gates (same as CI) ──────────────────────────────────────────────

check: lint typecheck security test ## Run every CI quality gate locally

lint: ## Ruff lint and format check
	ruff check src tests
	ruff format --check src tests

format: ## Auto-format and fix lint issues
	ruff format src tests
	ruff check --fix src tests

typecheck: ## Mypy (strict)
	mypy src/legalmind

security: ## Bandit security scan
	bandit -q -r src/legalmind --severity-level medium --confidence-level medium

test: ## Unit tests with coverage
	pytest tests/unit --cov=legalmind --cov-report=term-missing

test-integration: ## Integration tests (need deployed Azure services)
	pytest tests/integration -m integration

eval: ## Retrieval, generation and agent-routing evaluations (need Azure)
	python -m tests.evaluation.evaluate_retrieval
	python -m tests.evaluation.evaluate_generation
	python -m tests.evaluation.evaluate_agents

traffic: ## Realistic traffic against a deployed API: make traffic API_URL=https://...
	python scripts/generate_traffic.py --api $(API_URL) --rounds 2

# ─── Run locally ─────────────────────────────────────────────────────────────

run: ## API with auto-reload on http://localhost:8000
	uvicorn legalmind.main:app --reload --reload-dir src --port 8000

run-frontend: ## Streamlit UI on http://localhost:8501
	streamlit run src/frontend/app.py --server.port 8501

up: ## API + UI in Docker
	docker compose up --build

down: ## Stop the Docker stack
	docker compose down

# ─── Azure ───────────────────────────────────────────────────────────────────

deploy: ## Deploy to Azure: make deploy ENV=dev
	bash scripts/deploy.sh --environment $(ENV)

clean: ## Remove caches and build artefacts
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache -o -name "*.egg-info" \) -prune -exec rm -rf {} +
	rm -rf dist build htmlcov .coverage coverage.xml
