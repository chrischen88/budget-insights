# CLAUDE.md

Guidance for Claude when working in this repository. Read `SPEC.md` for full product and technical detail; this file covers how to work here.

## Project

**Spendsight** — a local-first Python app that imports Chase checking and credit card CSVs, stores them in DuckDB, and provides spending visualizations (Streamlit), ML-based categorization/recurring/anomaly detection, and LLM-powered merchant cleanup and natural-language Q&A.

Current phase: **Phase 3 complete**; Phase 4 (ask your data) is next. Phases 1 and 2 are complete (merchant eval passed with `openai` / `gpt-4o-mini`). Check the phase checklists in `SPEC.md §12` before starting work, and don't build later-phase features unless asked.

## Commands

```bash
uv sync                          # install deps
uv run streamlit run src/spendsight/app/Home.py   # run the app
uv run pytest                    # all tests
uv run pytest tests/ingest -k checking            # focused tests
uv run ruff check . && uv run ruff format .       # lint + format
uv run mypy src                  # type check
make eval                        # LLM evals (hits the API; costs money)
make eval-selfcheck              # offline eval harness check (free)
```

Before calling a task done: `ruff check`, `mypy src`, and `pytest` must all pass.

## Architecture in one breath

`ingest/` parses files → `db/` stores → `enrich/` marks transfers, normalizes merchants, categorizes → `ml/` finds recurring charges, anomalies, forecasts → `insights/` computes metrics → `llm/` handles every external model call → `app/` renders.

Dependency direction is one-way: `app` → `insights`/`ml`/`enrich`/`llm` → `db` → `money`/`config`. `ingest`, `enrich`, `ml`, and `db` must not import from `app`. Only `llm/providers/` imports provider SDKs (`anthropic`, `openai`).

## Non-negotiable rules

### Money
- Store and compute money as **integer cents** (`int` / DuckDB `BIGINT`). Parse strings with `decimal.Decimal`, then convert. **Never use `float` for money.**
- Sign convention everywhere: **negative = outflow, positive = inflow.**
- Use helpers in `src/spendsight/money.py` for parsing and display formatting; don't write ad hoc formatting.
- Spend totals exclude rows where `is_transfer` or `is_excluded` is true. Use the `v_spend` view instead of re-implementing that filter.

### LLM usage
- **The LLM never does arithmetic on financial data.** Numbers come from SQL/pandas; the LLM chooses tools and writes prose. If you find yourself asking a model to sum, average, or compare amounts, stop and compute it in code.
- All model calls go through `llm/client.py` (redaction → cache → call → schema validation → cost log). Never call the SDK directly elsewhere.
- Provider and model come from config (`SPENDSIGHT_LLM_PROVIDER` = `anthropic` | `openai`, `SPENDSIGHT_LLM_MODEL`). Don't hard-code either.
- Features call the provider-neutral `llm/client.py` and never branch on provider. Provider differences stay inside the `llm/providers/` adapters. Every client behavior (redaction, cache, validation) is tested against both adapters with mocks.
- No automatic failover to the other provider. A failed call falls through the cascade.
- Prompts live in `llm/prompts/` as versioned files (`merchant_normalize.v1.md`). Changing a prompt means bumping its version (which invalidates the cache) and running `make eval`, with the provider/model you intend to use.
- Structured outputs must be validated against a schema (pydantic). Invalid values (e.g. a category not in our list) are rejected and fall through the cascade, never stored.
- The chat agent's `run_sql` tool is SELECT-only on read-only views. Don't loosen that. Any change to `llm/tools.py` needs tests proving writes, `ATTACH`, `COPY`, `INSTALL`/`LOAD`, and multi-statement queries are rejected.

### Privacy
- **Never commit real financial data.** `data/` and `*.csv` outside `tests/fixtures/` are gitignored. Keep it that way.
- Test fixtures are **synthetic**. Don't copy rows from a real statement into fixtures, tests, prompts, docs, or commit messages, even partially.
- Store only the last 4 digits of account numbers.
- Every outbound LLM payload passes through `llm/redact.py`. When adding a new LLM feature, add a test asserting redaction runs on its payload.
- Don't log raw descriptions or amounts at INFO or above.
- The app must stay fully usable (minus LLM features) with `SPENDSIGHT_LOCAL_ONLY=true`.

### Data integrity
- Imports are idempotent. Transaction IDs are deterministic hashes (`SPEC.md §5.2`). Any change to the hash inputs is a migration, so flag it to the user first.
- Detect file format by header, not filename. Chase checking CSVs can have a trailing comma on each row; parsers must handle it.
- Every category assignment records `category_source` and `category_conf`. Don't write a category without them.
- Schema changes go in a new numbered migration in `db/migrations/`; never edit an applied migration.

## Conventions

- Python 3.12, full type hints, `mypy --strict` on `src/`.
- Pure functions for parsing, cleanup, and ML logic (take DataFrames/rows, return DataFrames/rows); DB access stays in `db/` repository functions. This keeps logic testable without a database.
- pandas for transforms; DuckDB SQL for aggregation over stored data.
- Dates: `datetime.date`; resolve relative dates ("last month") against an injected `today` parameter, not `date.today()` inside logic, so tests are deterministic.
- Charts: Plotly via Streamlit. Consistent category colors come from one mapping in `app/theme.py`.
- Keep Streamlit pages thin: they call `insights/` or repository functions and render. No SQL or business logic in `app/`.
- Naming: `snake_case` modules and functions; tables are plural; views are prefixed `v_`.

## Testing

- Every parser has fixture-based tests in `tests/ingest/`, including edge cases: trailing commas, refunds, fees, duplicate same-day charges, overlapping re-imports, empty files, unknown headers.
- Totals tests assert **exact cents** against hand-computed expected values.
- ML components are tested on synthetic data with known ground truth (e.g. a generator that plants monthly/annual subscriptions and outliers). Put generators in `tests/synth.py`.
- Unit tests must never hit the network. Mock `llm/client.py` at its boundary. Real API calls happen only in `make eval`.
- Merchant-cleanup regexes get a table-driven test: `(raw_description, expected_normalized_key)`.

## Working style for Claude

- Before implementing a feature, find its section in `SPEC.md` and follow it. If the spec is ambiguous or seems wrong, ask instead of guessing, and propose a spec edit.
- Prefer small, reviewable changes: one pipeline stage or one page per change.
- When adding a pipeline stage, update the pipeline diagram in `SPEC.md §5` if it changes.
- Don't add dependencies without saying why; prefer the stack in `SPEC.md §10`.
- If a task would send new kinds of data to an external API, call that out explicitly before implementing.
- When the user shares real statements for debugging, work with them locally and don't echo transaction details back beyond what's needed.

## Environment

`.env` (copy from `.env.example`):

```
SPENDSIGHT_LLM_PROVIDER=anthropic     # or: openai
SPENDSIGHT_LLM_MODEL=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
SPENDSIGHT_LOCAL_ONLY=false
SPENDSIGHT_REDACT_NAMES=               # comma-separated names masked in LLM payloads
SPENDSIGHT_DB_PATH=data/spendsight.duckdb
SPENDSIGHT_ML_CONF_THRESHOLD=0.75
```
