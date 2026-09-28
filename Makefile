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

# Hits the real LLM API and costs money. Not part of `check`.
eval:
	@test -f evals/run_evals.py || { echo "evals/run_evals.py not implemented yet (Phase 2)"; exit 1; }
	uv run python evals/run_evals.py

.PHONY: install app test lint format typecheck check eval
