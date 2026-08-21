check: check-ruff check-format check-mypy

check-ruff:
	@uv run ruff check src tests

check-format:
	@uv run ruff format --check src tests

check-mypy:
	@uv run mypy

codefix:
	@uv run ruff check --fix src tests
	@uv run ruff format src tests

test:
	@uv run pytest

test-cov:
	@uv run pytest --cov --cov-report=term-missing --cov-fail-under=90

docs:
	@uv run python -m aiosolarfocus registers --system Vampair --api-version 26.020 --markdown > docs/registers.md

.PHONY: check check-ruff check-format check-mypy codefix test test-cov docs
