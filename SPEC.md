# Spendsight — Product & Technical Spec

> Working name. A local-first personal finance app that imports Chase checking and credit card statements, visualizes spending, and uses ML + LLMs to categorize, detect patterns, and answer questions in plain English.

**Status:** Draft v0.1 · **Owner:** Christopher · **Last updated:** 2026-09-28

---

## 1. Goals & non-goals

### Goals
1. Import Chase checking and credit card transactions (CSV first, PDF later) with no manual cleanup.
2. Show where money goes: by category, merchant, month, and account.
3. Categorize transactions more precisely than Chase's built-in categories, and learn from user corrections.
4. Detect subscriptions/recurring charges, unusual transactions, and forecast month-end spend.
5. Let the user ask natural-language questions ("What did I spend on dining in Q3 vs Q2?") and get answers backed by real queries.
6. Keep financial data on the user's machine by default; send the minimum necessary to any external API.

### Non-goals (v1)
- Live bank connections (Plaid, OAuth to Chase). Import is file-based.
- Multi-user accounts, auth, or cloud hosting.
- Investment/brokerage accounts, taxes, or bill pay.
- Mobile app.

---

## 2. Core design principles

| Principle | What it means in practice |
|---|---|
| **Code computes, LLM narrates** | Every number shown to the user comes from SQL/pandas. The LLM never performs arithmetic on transaction data; it selects queries/tools and explains results. |
| **Deterministic before learned** | Rules → user overrides → ML classifier → LLM fallback. Cheaper, explainable methods run first. |
| **Local-first & privacy-minimal** | Data lives in a local DuckDB file. Account numbers are never stored in full and never sent to an LLM. LLM calls send merchant strings or aggregates, not raw statements, unless the user opts into PDF extraction. |
| **Idempotent imports** | Re-importing the same file never creates duplicates. |
| **Every inference is traceable** | Each category/merchant assignment records its `source` (rule, user, ml, llm) and confidence so it can be audited and corrected. |

---

## 3. Inputs

### 3.1 Chase credit card CSV
Header: `Transaction Date,Post Date,Description,Category,Type,Amount,Memo`

- Dates: `MM/DD/YYYY`
- `Amount`: negative = purchase/fee, positive = payment/refund
- `Type`: `Sale`, `Payment`, `Return`, `Fee`, `Adjustment`
- `Category`: Chase's coarse category (kept as `chase_category`, used as an ML feature)

### 3.2 Chase checking CSV
Header: `Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #`

- `Details`: `DEBIT`, `CREDIT`, `CHECK`, `DSLIP`
- `Amount`: negative = outflow
- `Type`: e.g. `ACH_DEBIT`, `ACH_CREDIT`, `DEBIT_CARD`, `ATM`, `QUICKPAY_DEBIT`, `LOAN_PMT`, `MISC_DEBIT`
- Known quirk: rows may have a trailing comma (extra empty column). The parser must tolerate this and must not misalign columns.

### 3.3 Format detection
Detect file type by header row, not filename. Unknown headers → clear error listing expected formats.

### 3.4 PDF statements (Phase 5, optional)
Extract text with `pdfplumber`; send to LLM with a strict JSON schema; **validate** that extracted rows sum to the statement's printed totals (purchases, payments, ending balance). Reject the import on mismatch and show the diff.

---

## 4. Data model (DuckDB)

All money stored as **integer cents** (`BIGINT`). Sign convention: **negative = money out, positive = money in**, for every account type.

```sql
accounts (
  id            TEXT PRIMARY KEY,      -- e.g. 'chase-card-1234'
  kind          TEXT NOT NULL,         -- 'credit_card' | 'checking'
  display_name  TEXT NOT NULL,
  last4         TEXT                   -- only last 4 digits, never full number
);

imports (
  id            TEXT PRIMARY KEY,      -- uuid
  account_id    TEXT REFERENCES accounts(id),
  filename      TEXT,
  file_sha256   TEXT UNIQUE,           -- blocks re-importing identical file
  imported_at   TIMESTAMP,
  row_count     INTEGER
);

transactions (
  id               TEXT PRIMARY KEY,   -- deterministic hash, see §5.2
  account_id       TEXT REFERENCES accounts(id),
  import_id        TEXT REFERENCES imports(id),
  txn_date         DATE NOT NULL,
  post_date        DATE,
  amount_cents     BIGINT NOT NULL,
  raw_description  TEXT NOT NULL,
  chase_category   TEXT,
  chase_type       TEXT,
  memo             TEXT,
  merchant_id      INTEGER,            -- merchants.id (not a FK: see note below)
  category_id      INTEGER,            -- categories.id (not a FK: see note below)
  category_source  TEXT,               -- 'rule' | 'user' | 'ml' | 'llm' | 'chase'
  category_conf    DOUBLE,
  is_transfer      BOOLEAN DEFAULT FALSE,
  is_excluded      BOOLEAN DEFAULT FALSE,
  transfer_candidate BOOLEAN DEFAULT FALSE, -- looks like a transfer (§5.3); unpaired = needs review
  transfer_pair_id TEXT,               -- id of the matched opposite side, when paired
  category_locked  BOOLEAN DEFAULT FALSE -- per-transaction user override; cascade skips it
);

merchants (
  id             INTEGER PRIMARY KEY,
  normalized_key TEXT UNIQUE,          -- description after deterministic cleanup
  clean_name     TEXT,                 -- 'Blue Bottle Coffee'
  default_category_id INTEGER,         -- categories.id (not a FK: see note below)
  source         TEXT,                 -- 'llm' | 'user'
  confidence     DOUBLE
);

categories (
  id        INTEGER PRIMARY KEY,
  name      TEXT UNIQUE NOT NULL,      -- 'Coffee Shops'
  parent_id INTEGER,                   -- categories.id, e.g. 'Food & Dining' (not a FK)
  is_income BOOLEAN DEFAULT FALSE
);

category_rules (
  id          INTEGER PRIMARY KEY,
  pattern     TEXT NOT NULL,           -- regex on raw_description
  category_id INTEGER REFERENCES categories(id),
  priority    INTEGER DEFAULT 100,
  created_by  TEXT                     -- 'seed' | 'user'
);

chase_category_mappings (
  chase_category TEXT PRIMARY KEY,     -- Chase card category, e.g. 'Food & Drink'
  category_id    INTEGER NOT NULL REFERENCES categories(id)
);                                     -- seeded; editable in Settings

recurring_series (
  id            INTEGER PRIMARY KEY,
  merchant_id   INTEGER,               -- merchants.id (plain column, see below)
  cadence_days  INTEGER,               -- ~7, ~14, ~30, ~91, ~365
  typical_cents BIGINT,
  last_seen     DATE,
  next_expected DATE,
  status        TEXT,                  -- 'active' | 'dismissed' (lapsed is derived at read time)
  cancelled_on  DATE                   -- user marked it cancelled on this date
);                                     -- unique per merchant_id

anomalies (
  transaction_id TEXT,                 -- transactions.id (plain column, see below)
  kind           TEXT,                 -- 'amount_outlier' | 'duplicate' | 'new_merchant_large' | 'price_increase'
  score          DOUBLE,
  explanation    TEXT,
  dismissed      BOOLEAN DEFAULT FALSE
);                                     -- unique per (transaction_id, kind)

llm_cache (
  prompt_hash TEXT PRIMARY KEY,        -- sha256(model + prompt_version + input)
  response    JSON,
  created_at  TIMESTAMP
);
```

Seed `categories` with a two-level taxonomy (~12 parents, ~40 children). Users can add/rename.

**Foreign keys on edited columns.** DuckDB runs an UPDATE of an indexed (e.g. foreign-key) column as delete + insert, which fails when other rows reference the table. So reference columns the app edits (`categories.parent_id`, `merchants.default_category_id`, `transactions.merchant_id`, `transactions.category_id`) are plain integers (migration 008). Repository writers validate them and `repository.integrity_problems()` checks for dangling references. Rule for new tables: a table that others reference must not have a foreign key on a column the app updates.

DuckDB also rejects `UPDATE ... RETURNING` on any row another table references, even when the key is unchanged. So derived ML output (`recurring_series.merchant_id`, `anomalies.transaction_id`) holds plain references too (migration 011): a foreign key there would block recategorizing, "apply to merchant" and LLM naming. Rule: **nothing may hold a foreign key to `transactions` or `merchants`**; add the column to `integrity_problems()` instead.

---

## 5. Pipeline

```
file → detect format → parse → normalize → dedupe → store
     → transfer detection → merchant normalization → categorization
     → recurring detection → anomaly detection → metrics
```

### 5.1 Normalize
- Parse dates to `DATE`, amounts to integer cents (parse via `Decimal`, never `float`).
- Collapse whitespace, uppercase `raw_description` copy for matching (keep original too).

### 5.2 Dedupe
`id = sha256(account_id | txn_date | amount_cents | raw_description | occurrence_index)`
where `occurrence_index` is the 0-based count of identical (date, amount, description) rows earlier in the same file. This keeps two legitimate identical same-day charges while making re-imports of overlapping date ranges idempotent.

### 5.3 Transfer detection
Credit card payments appear twice (checking outflow + card inflow). Mark both sides `is_transfer = TRUE` and exclude from spend totals.
- Card side: `chase_type = 'Payment'`.
- Checking side: description matches seeded patterns (e.g. `CHASE CREDIT CRD AUTOPAY`, `Payment to Chase card ending in NNNN`).
- Pair when amounts are equal and opposite, in different accounts, within ±5 days. If a description names a card (`ending in NNNN`), it only pairs with the card account with that last4. Matching is one-to-one, closest dates first.
- Paired rows: `is_transfer = TRUE` and `transfer_pair_id` points at each other. Unpaired candidates get `transfer_candidate = TRUE` but keep `is_transfer = FALSE`, so they still count until reviewed. They are flagged, not silently excluded.
- Same logic for transfers between the user's own checking/savings accounts (`Online Transfer to/from`).
- Detection re-runs after every import and only considers unpaired rows, so a payment pairs once its other side is imported later, and existing pairs never change.

### 5.4 Merchant normalization
1. **Deterministic cleanup** → `normalized_key` (`enrich/merchants.py`): strip processor prefixes (`SQ *`, `TST*`, `PAYPAL *`, `SP `, `DD *`), debit-card wrappers (`CARD PURCHASE 01/05 …`), ACH IDs, phone numbers, dates, `*reference` codes, and everything from the first store number or billing domain onward, plus a trailing state code. State codes that are also words (`IN`, `OR`, `ME`, `OK`, `HI`, `CO`) are only dropped from fixed-width location fields. Cleanup is idempotent: normalizing a key returns the same key.
   Every transaction is linked to a `merchants` row by key at import. Until a clean name exists, views show the key as the merchant name.
2. Look up `normalized_key` in `merchants`. Hit → done.
3. Miss → batch unknown keys (up to 50 per call) to the LLM, which returns `{clean_name, suggested_category, confidence}` per key. Cache in `llm_cache` and insert into `merchants`.
   - Only merchants with `source IS NULL` are sent; a processed merchant gets `source = 'llm'` even when the model gave no usable category, so it is not sent again. User-set merchants are never overwritten.
   - **Never sent:** peer-to-peer payments (Zelle, Venmo, Cash App, Apple Cash, PayPal transfers), whose keys carry people's names and don't say what the payment was for, and merchants that only appear on transfers. Categorize P2P with user rules.
   - Merchants go out as `{id, description}` and come back by `id`, so redaction can rewrite a description without breaking the match. Names containing a redaction placeholder are discarded.
   - A failed batch leaves its merchants unnamed for the next import; the import itself never fails because of the LLM.

### 5.5 Categorization (cascade, first match wins)
1. **User override** on that specific transaction: sets `category_locked = TRUE` (`source = 'user'`, conf 1.0). The cascade never touches locked rows.
2. **User/seed rule** (`category_rules`): regex matched case-insensitively anywhere in `raw_description`; lower `priority` number runs first, ties by rule id. User rules default to 100, seed rules use 200, so user rules win. Invalid patterns are skipped and logged by rule id. (`source = 'rule'`, conf 1.0.)
3. **Merchant default** (`merchants.default_category_id`, set by user or LLM): `source` is the merchant's source (`'user'` conf 1.0, or `'llm'` with its confidence). Unlike an override it is recomputed, so changing a merchant's default moves all its unlocked transactions.
4. **ML classifier** if confidence ≥ threshold (default 0.75).
5. **LLM** suggestion from merchant normalization: a merchant default with `source = 'llm'` (conf = the model's confidence). It ranks below a user-set merchant default and below the ML classifier.
6. **Fallback** mapping from `chase_category` → our taxonomy via `chase_category_mappings` (matched case-insensitively; `category_source = 'chase'`, `category_conf = 0.5`).
7. Nothing matches → uncategorized.

The cascade re-runs over every unlocked transaction after each import, so edits to rules, merchant defaults or the Chase mapping take effect everywhere they apply.

Record `category_source` and `category_conf` for every assignment.

**Learning loop:** when the user recategorizes a transaction, offer "Apply to all from this merchant?" (updates `merchants.default_category_id`); the edit is also a training label. The classifier is retrained on **every cascade run** (after each import and each edit) rather than on demand or every N corrections: logistic regression on a few thousand rows takes milliseconds, so there is no stored model to go stale. The edit confirmation says how many other transactions the retrained classifier moved.

---

## 6. ML components

| Component | Method | Inputs | Output | Acceptance |
|---|---|---|---|---|
| **Category classifier** | TF-IDF (char 3–5 grams on `normalized_key`) + amount bucket + `chase_category` one-hot → Logistic Regression | Transactions with `category_source IN ('user','rule')` | Category + probability | ≥ 85% accuracy on held-out user-labeled set once ≥ 300 labels exist; disabled below 100 labels |
| **Recurring detection** | Group by merchant; for groups with ≥ 3 charges compute median gap and gap std-dev; match to cadence buckets (7/14/30/91/365 ± tolerance); amount CV < 0.15 (or fixed amount) | Transactions | `recurring_series` rows, `next_expected` | Finds ≥ 90% of subscriptions in a synthetic fixture with known ground truth |
| **Anomaly detection** | (a) Robust z-score (median/MAD) of amount per category; (b) exact duplicate within 3 days; (c) first-ever charge from merchant > $X (default $200); (d) recurring charge price increase > 5% | Transactions, recurring_series | `anomalies` rows with plain-English `explanation` | False-positive rate low enough that ≤ 5 alerts/month on typical data (tunable thresholds) |
| **Forecast** | Month-to-date spend + expected remaining recurring charges + per-category daily run-rate from trailing 90 days. Upgrade path: Prophet on monthly totals once ≥ 18 months of data | Transactions, recurring_series | Projected month-end spend by category with range | Backtest MAPE reported in UI; no hard target in v1 |

Isolation Forest is a v2 option; start with the explainable methods above.

**Recurring detection details** (`ml/recurring.py`):
- Inputs: outflows with a merchant, excluding transfers, transfer candidates and excluded rows (an unpaired autopay is not a subscription). One charge per day; on a double-charge day the charge closest to the merchant's median is kept, so the duplicate doesn't hide the series (it's the anomaly detector's job).
- Cadence windows (gap in days): weekly 6–8, biweekly 12–16, monthly 26–35, quarterly 82–100, annual 350–380. The median gap picks the bucket; ≥ 75% of gaps must fit it or be one skipped period (~2× the cadence). Amount CV < 0.15.
- One series per merchant (limitation: two subscriptions billed under one merchant key, e.g. two Apple services, are only found if one dominates).
- `next_expected` steps by calendar month for monthly/quarterly/annual (Jan 31 → Feb 28/29), by days for weekly/biweekly. Annualized cost is exact integer cents: typical × 52/26/12/4/1.
- Re-detection runs after every import, updates series in place, keeps user dismissals/cancellations, and removes series whose pattern no longer holds unless the user touched them.
- Display status (`insights/recurring.py`): dismissed, then cancelled (user choices win), else **lapsed** when the reference date is past `next_expected` by more than the cadence window's slack (weekly 1, biweekly 2, monthly 5, quarterly 9, annual 15 days), else active. The reference date is the earlier of an injected `today` and the newest imported transaction, so a stale import doesn't lapse everything. Annualized totals count active series only.
- Tested on `tests/synth.py`: planted weekly/biweekly/monthly/quarterly/annual series (price increases, a skipped month, a cancelled one, month-end billing, posting jitter) among noise; recall and precision ≥ 90% on 10 seeds.

**Anomaly detection details** (`ml/anomalies.py`). Thresholds live in `AnomalyConfig`. Explanations are code templates, never LLM output.
- Inputs: same outflows as recurring detection (no transfers, transfer candidates or excluded rows).
- *Amount outlier*: modified z-score `0.6745 × (amount − median) / MAD` within the transaction's category > 3.5, **and** ≥ 2× the category median, **and** ≥ $50 above it (so a $30 coffee order isn't an alert). Needs ≥ 8 charges in the category; categories with MAD 0 (fixed amounts like rent) and uncategorized rows are skipped. *"$450.00 at Grocer is more than 5 times the typical Groceries charge ($90.00)."*
- *Duplicate*: same account, merchant and exact amount within 3 days, amount ≥ $20 (two identical coffees aren't worth an alert). The later charge is flagged.
- *New merchant, large*: the merchant's first-ever charge is ≥ $200 and falls ≥ 60 days after that account's first transaction (otherwise the first import flags every merchant).
- *Price increase*: for merchants with a recurring series the user hasn't dismissed, a charge > 5% above the **highest** of its previous 3 charges (one alert per rise; a wobbling price isn't re-flagged). Same-day double charges collapse first, as in recurring detection.
- One anomaly per (transaction, kind). Re-detection runs after every import, after recategorizing, and after dismissing/restoring a recurring series; it updates in place, keeps dismissals, and removes undismissed anomalies that no longer hold.
- Tested on `tests/synth.py` `anomaly_dataset`: two years of heavy-tailed everyday spending with one planted anomaly of each kind; all found on 10 seeds at ≤ 1 alert/month (target ≤ 5).

**Classifier details** (`ml/classifier.py`, run by `enrich/categorize_runner.py`):
- Labels: locked per-transaction edits, plus this run's `rule` and `user` (merchant default) assignments, computed in a first cascade pass so they are current. Rows the classifier labeled (`ml`) are never labels, so it can't reinforce its own guesses. Off below 100 labels or with fewer than two categories.
- Features: TF-IDF of character 3–5 grams (word-bounded) on the merchant's `normalized_key` (the raw description when there is no merchant), amount bucket with sign (< $5, < $20, < $50, < $100, < $250, < $1,000, ≥ $1,000), Chase category; one-hot. Logistic regression (C = 5).
- Each merchant's rows share one unit of weight, so one busy merchant doesn't drown out the rest.
- Predictions go to every row that rules and user merchant defaults didn't decide; step 4 applies those with probability ≥ `SPENDSIGHT_ML_CONF_THRESHOLD` (default 0.75), recorded as `category_source = 'ml'`, `category_conf` = the probability (3 places).
- **Evaluation holds out merchants, not rows**: a labeled merchant is already caught by its merchant default, so the classifier only matters for merchants the user hasn't labeled, and a row split would leak. `merchant_holdout()` reports accuracy over all held-out rows (the acceptance target), plus accuracy and coverage of predictions at the threshold, which is what the cascade actually applies.
- Tested on `tests/synth.py` `classifier_dataset` (≥ 300 labels, 55 merchants, eight categories, coarse Chase categories): all-rows held-out accuracy averages ≥ 85% over 10 seeds (one seed dips to ~80% when the only merchant with a telling word is held out; those misses are all below threshold); applied predictions are ≥ 95% accurate on every seed at ≥ 60% coverage.

---

## 7. LLM components

All LLM calls go through `llm/client.py`, which enforces: redaction (§8), caching, a versioned prompt template, a JSON schema for structured output, retries, and a token/cost log.

Providers: **Anthropic** (Claude API) and **OpenAI**. One provider is active at a time, chosen by `SPENDSIGHT_LLM_PROVIDER` (`anthropic` | `openai`, default `anthropic`). **Provider and model name are config (`SPENDSIGHT_LLM_PROVIDER`, `SPENDSIGHT_LLM_MODEL` in `.env`), never hard-coded.** Optional local fallback via Ollama for merchant normalization.

- `llm/client.py` exposes one provider-neutral interface (structured output, tool use). Each provider is an adapter in `llm/providers/` (`anthropic.py`, `openai.py`); features never branch on provider.
- Redaction, caching, schema validation and cost logging live in `client.py` and run the same way for every provider.
- **No automatic failover between providers.** If the active provider errors, the call fails and the cascade falls through. Silently retrying on the other vendor would send data to a service the user didn't choose.
- The cache key is sha256 of (provider, model, prompt name, prompt version, output schema, redacted input), so switching provider, model, prompt or category list never serves a stale answer.
- `llm_calls` logs every request (provider, model, prompt + version, cache hit, ok/error class, token counts), never content.

### 7.1 Merchant normalization (Phase 2)
- Input: list of `normalized_key` strings + our category list.
- Output (JSON schema): `[{key, clean_name, category, confidence}]`.
- Category must be one of the provided names; invalid values → reject and fall through the cascade.

### 7.2 Ask-your-data chat (Phase 4)
Tool-use agent. The LLM **cannot** see the transactions table directly; it calls tools:

| Tool | Purpose |
|---|---|
| `get_schema()` | Returns a read-only view description (`v_spend`: date, amount, merchant, category, parent_category, account) |
| `run_sql(query)` | Executes **SELECT-only** SQL against read-only views; enforced via DuckDB read-only connection + statement parser rejecting non-SELECT; row limit 500; 5s timeout |
| `spend_summary(start, end, group_by)` | Pre-built aggregate for common questions (preferred over raw SQL) |
| `list_recurring()` / `list_anomalies(start, end)` | Wrap ML outputs |

Answer requirements:
- Every number in the answer must come from a tool result. The UI shows the executed query/tool calls in an expandable "How I got this" section.
- If the question is ambiguous (e.g. "last month"), resolve relative to today's date and state the interpretation.

### 7.3 Monthly summary (Phase 5)
- Code computes a fixed metrics payload: totals by parent category, MoM and 3-month-average deltas, top 10 merchants, new recurring charges, lapsed recurring charges, open anomalies, forecast vs actual.
- LLM receives **only this aggregate payload** and writes a ≤ 200-word summary + up to 3 suggestions.
- Stored per month; regenerated only when underlying data changes.

### 7.4 PDF extraction (Phase 5, opt-in)
See §3.4. This is the only feature that sends raw statement text; requires explicit per-import consent in the UI, with account numbers redacted before sending.

### 7.5 LLM evaluation
- `evals/merchants.jsonl`: ~200 hand-labeled raw descriptions → expected clean name + category. Target ≥ 90% category agreement.
  - Cases list every acceptable category (`accept_categories`); a few expect `Unknown` so abstaining is tested too. Agreement counts an accepted category or its parent (reported separately). Names are reported, not gated, with a "hard names" score over cases where title-casing the key isn't already the answer.
  - The runner uses the app's real path (production key cleanup → `suggest_merchants` → `LLMClient` → configured adapter), a fresh database per rep (no cache carry-over), and writes `results.jsonl`, `errors.jsonl` (failures never scored as wrong) and `summary.json` under `evals/results/` (gitignored).
  - `make eval-selfcheck` runs offline oracle/null modes (free); `make eval` runs live.
- `evals/questions.jsonl`: ~30 NL questions with expected numeric answers against a fixed synthetic database. Target: 100% of numbers match (tolerance: exact cents).
- Evals run via `make eval` against the configured provider and model; required to pass before changing prompts, model, or provider.

---

## 8. Privacy & security

- Local DuckDB file at `data/spendsight.duckdb`; `data/` is gitignored.
- Only the last 4 digits of any account number are stored.
- `llm/redact.py` runs on every outbound payload: masks sequences of ≥ 6 digits, emails, phone numbers, and names from a user-configured list (`SPENDSIGHT_REDACT_NAMES`, comma-separated; kept out of logs and reprs). Unit-tested.
- Default outbound LLM content: merchant description strings and aggregates only.
- API keys read from `.env` (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`); only the active provider's key is used; never logged.
- Settings toggle "Local only" disables all external LLM calls (app degrades gracefully: rules + ML + Chase categories).
- Logs never include full descriptions at INFO level or above.

---

## 9. UI (Streamlit)

| Page | Contents |
|---|---|
| **Import** | Drag-and-drop CSV(s); detected account/format; preview first 10 rows; import summary (new, duplicates skipped, transfers found) |
| **Overview** | Date range + account filters; KPI row (spent, income, net, vs. the same-length prior period, shown only when that period has data); monthly spend stacked by parent category; top merchants; spend by category (ranked bar) |
| **Transactions** | Search (description, merchant, memo) + date/account/category filters, transfers hidden by default; source/confidence badge; select a row to edit its category (locks that row), optionally "apply to all from this merchant" (sets the merchant default and re-runs the cascade, reporting rows a rule or earlier edit keeps elsewhere); "Reset to automatic" unlocks |
| **Recurring** | Active subscriptions with cadence, amount, next date, annualized cost; dismiss / mark cancelled |
| **Alerts** | Anomalies with explanations, last 90 days of data by default; dismiss / restore (show dismissed toggle) |
| **Forecast** | Month-to-date vs projected, by category |
| **Ask** | Chat box; answers with expandable tool/SQL trace |
| **Settings** | Categories, rules, LLM on/off, model, thresholds, redaction names |

Transfers are excluded from spend charts by default, with a toggle to show them.

**Spend vs income** (one definition, the `flow` column of `v_transactions` / `v_spend`):
- *Spend* = every outflow, plus inflows in a non-income category (a refund nets against the category it came from).
- *Income* = inflows in an income category, and uncategorized inflows.
- *Net* = income − spend = the plain sum of amounts.

**Charts** follow one color mapping in `app/theme.py`: seven major parent categories own fixed palette slots and the rest fold into a neutral "Other", so a category's color never changes with rank or filters. Every chart has hover values and a table view.

---

## 10. Tech stack

- Python 3.12, `uv` for env/deps
- pandas, DuckDB
- scikit-learn (classifier), optional `prophet`
- `anthropic` and `openai` SDKs; optional `ollama`
- Streamlit + Plotly
- pdfplumber (Phase 5)
- pytest, ruff, mypy

---

## 11. Project structure

```
spendsight/
├── CLAUDE.md
├── SPEC.md
├── pyproject.toml
├── .env.example
├── Makefile
├── data/                    # gitignored: DB, raw imports, model artifacts
├── src/spendsight/
│   ├── config.py
│   ├── money.py             # cents parsing/formatting helpers
│   ├── db/                  # schema.sql, migrations, repository functions, views
│   ├── ingest/              # detect.py, chase_card.py, chase_checking.py, dedupe.py, pdf.py
│   ├── enrich/              # transfers.py, merchants.py, categorize.py, rules.py
│   ├── ml/                  # classifier.py, recurring.py, anomalies.py, forecast.py
│   ├── llm/                 # client.py, redact.py, cache.py, tools.py, prompts/, providers/
│   ├── insights/            # metrics.py, summary.py
│   └── app/                 # Streamlit: Home.py router + views/ (not pages/: see Home.py)
├── tests/
│   └── fixtures/            # synthetic Chase CSVs only — never real data
└── evals/                   # merchants.jsonl, questions.jsonl, run_evals.py
```

---

## 12. Phased delivery & acceptance criteria

### Phase 1 — Import & dashboard
- [x] Imports both Chase CSV formats, including trailing-comma checking rows
- [x] Re-importing the same or overlapping file adds zero duplicates
- [x] Card payments detected as transfers on both sides and excluded from spend
- [x] Overview page with monthly spend by category (Chase categories mapped to taxonomy)
- [x] Totals match a hand-computed sum on fixtures to the cent

### Phase 2 — Smart categorization
- [x] Deterministic merchant cleanup with unit tests for ≥ 30 real-world description patterns
- [x] LLM merchant normalization with caching; second run makes zero API calls
- [x] Categorization cascade with `category_source` recorded (ML/LLM steps slot in with their features)
- [x] Inline recategorization + "apply to merchant"
- [x] Merchant eval ≥ 90% (95.8% with openai / gpt-4o-mini, 2026-09-28)

### Phase 3 — Patterns
- [x] Recurring detection page with annualized cost
- [x] Anomaly alerts with explanations
- [x] ML classifier trains from user labels and slots into the cascade

### Phase 4 — Ask your data
- [ ] Tool-use chat with SELECT-only enforcement (tests prove INSERT/UPDATE/DROP/ATTACH are rejected)
- [ ] Question eval: 100% numeric accuracy
- [ ] Tool trace visible in UI

### Phase 5 — Forecasts, summaries, PDF
- [ ] Month-end forecast
- [ ] Monthly LLM summary from aggregates only
- [ ] Opt-in PDF import with totals reconciliation

---

## 13. Open questions

1. Are there other accounts (non-Chase cards, savings, Venmo) worth supporting via a generic CSV column-mapper in v1?
2. Should Zelle/Venmo payments to people be categorized by payee (e.g. "Rent") via user rules?
3. Split transactions (one Costco charge = groceries + household) — needed in v1?
4. Budgets per category with progress bars — Phase 3 or later?
5. Any need to export (CSV/Sheets) cleaned data?
