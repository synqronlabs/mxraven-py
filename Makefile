.DEFAULT_GOAL := help

.PHONY: help install lock format lint typecheck test coverage check build docs pre-commit

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install the project and dev dependencies into .venv
	uv sync

lock: ## Refresh uv.lock
	uv lock

format: ## Format and apply safe fixes
	uv run ruff format
	uv run ruff check --fix

lint: ## Check formatting and lint without modifying files
	uv run ruff format --check
	uv run ruff check

typecheck: ## Run mypy
	uv run mypy

test: ## Run the test suite
	uv run pytest

coverage: ## Run tests with a coverage report
	uv run pytest --cov --cov-report=term-missing

check: ## Run every quality gate (format, lint, typecheck, test, build)
	uv run ruff format --check
	uv run ruff check
	uv run mypy
	uv run pytest --cov
	uv build

build: ## Build the sdist and wheel
	uv build

docs: ## Build the documentation site
	uv run mkdocs build --strict

pre-commit: ## Install the git hooks
	uv run pre-commit install
