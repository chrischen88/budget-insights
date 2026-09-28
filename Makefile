install:
	uv sync

app:
	uv run streamlit run src/spendsight/app/Home.py

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy src

# Everything that must pass before a task is done.
check: lint typecheck test

# Hits the real LLM API (configured provider/model) and costs money. Not part of `check`.
eval:
	uv run python -m evals.run_evals --yes

# Free offline harness self-check: oracle must score 100%, null only the abstain cases.
eval-selfcheck:
	uv run python -m evals.run_evals --mode oracle
	uv run python -m evals.run_evals --mode null

.PHONY: install app test lint format typecheck check eval eval-selfcheck
