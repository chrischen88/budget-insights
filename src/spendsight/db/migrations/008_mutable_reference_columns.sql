-- Drop foreign keys on reference columns the app edits.
--
-- DuckDB (1.5) runs an UPDATE of an indexed column as delete + insert, and a foreign key
-- column is indexed. On a table that other rows reference, that delete fails
-- ("still referenced by a foreign key"). So these columns could not be edited once used:
--   categories.parent_id              (move a category)
--   merchants.default_category_id     ("apply to merchant")
--   transactions.merchant_id/category_id  (recategorize, once anomalies reference rows)
-- They become plain INTEGERs; integrity is checked by repository.integrity_problems().
-- All other foreign keys are kept (e.g. deleting a category still in a rule is blocked).
--
-- DuckDB can't drop a constraint in place, so affected tables are rebuilt: copy to holding
-- tables, drop children-first, recreate parents-first, copy back. Views rebind by name.

CREATE TABLE hold_categories AS SELECT * FROM categories;
CREATE TABLE hold_merchants AS SELECT * FROM merchants;
CREATE TABLE hold_transactions AS SELECT * FROM transactions;
CREATE TABLE hold_category_rules AS SELECT * FROM category_rules;
CREATE TABLE hold_chase_category_mappings AS SELECT * FROM chase_category_mappings;
CREATE TABLE hold_recurring_series AS SELECT * FROM recurring_series;
CREATE TABLE hold_anomalies AS SELECT * FROM anomalies;

DROP TABLE anomalies;
DROP TABLE recurring_series;
DROP TABLE chase_category_mappings;
DROP TABLE category_rules;
DROP TABLE transactions;
DROP TABLE merchants;
DROP TABLE categories;

CREATE TABLE categories (
    id        INTEGER PRIMARY KEY DEFAULT nextval('seq_categories'),
    name      TEXT UNIQUE NOT NULL,
    parent_id INTEGER,                      -- categories.id; not a FK (see header)
    is_income BOOLEAN DEFAULT FALSE
);

CREATE TABLE merchants (
    id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_merchants'),
    normalized_key      TEXT UNIQUE,
    clean_name          TEXT,
    default_category_id INTEGER,            -- categories.id; not a FK (see header)
    source              TEXT CHECK (source IS NULL OR source IN ('llm', 'user')),
    confidence          DOUBLE
);

CREATE TABLE transactions (
    id                 TEXT PRIMARY KEY,    -- deterministic hash, SPEC.md §5.2
    account_id         TEXT REFERENCES accounts(id),
    import_id          TEXT REFERENCES imports(id),
    txn_date           DATE NOT NULL,
    post_date          DATE,
    amount_cents       BIGINT NOT NULL,
    raw_description    TEXT NOT NULL,
    chase_category     TEXT,
    chase_type         TEXT,
    memo               TEXT,
    merchant_id        INTEGER,             -- merchants.id; not a FK (see header)
    category_id        INTEGER,             -- categories.id; not a FK (see header)
    category_source    TEXT CHECK (category_source IS NULL OR category_source IN ('rule', 'user', 'ml', 'llm', 'chase')),
    category_conf      DOUBLE,
    is_transfer        BOOLEAN DEFAULT FALSE,
    is_excluded        BOOLEAN DEFAULT FALSE,
    transfer_pair_id   TEXT,
    transfer_candidate BOOLEAN DEFAULT FALSE,
    category_locked    BOOLEAN DEFAULT FALSE,
    CHECK (category_id IS NULL OR (category_source IS NOT NULL AND category_conf IS NOT NULL))
);

CREATE TABLE category_rules (
    id          INTEGER PRIMARY KEY DEFAULT nextval('seq_category_rules'),
    pattern     TEXT NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    priority    INTEGER DEFAULT 100,
    created_by  TEXT CHECK (created_by IS NULL OR created_by IN ('seed', 'user'))
);

CREATE TABLE chase_category_mappings (
    chase_category TEXT PRIMARY KEY,
    category_id    INTEGER NOT NULL REFERENCES categories(id)
);

CREATE TABLE recurring_series (
    id            INTEGER PRIMARY KEY DEFAULT nextval('seq_recurring_series'),
    merchant_id   INTEGER REFERENCES merchants(id),
    cadence_days  INTEGER,
    typical_cents BIGINT,
    last_seen     DATE,
    next_expected DATE,
    status        TEXT CHECK (status IS NULL OR status IN ('active', 'lapsed', 'dismissed'))
);

CREATE TABLE anomalies (
    transaction_id TEXT REFERENCES transactions(id),
    kind           TEXT CHECK (kind IN ('amount_outlier', 'duplicate', 'new_merchant_large', 'price_increase')),
    score          DOUBLE,
    explanation    TEXT,
    dismissed      BOOLEAN DEFAULT FALSE
);

INSERT INTO categories (id, name, parent_id, is_income)
SELECT id, name, parent_id, is_income FROM hold_categories;

INSERT INTO merchants (id, normalized_key, clean_name, default_category_id, source, confidence)
SELECT id, normalized_key, clean_name, default_category_id, source, confidence FROM hold_merchants;

INSERT INTO transactions (
    id, account_id, import_id, txn_date, post_date, amount_cents, raw_description,
    chase_category, chase_type, memo, merchant_id, category_id, category_source,
    category_conf, is_transfer, is_excluded, transfer_pair_id, transfer_candidate,
    category_locked
)
SELECT
    id, account_id, import_id, txn_date, post_date, amount_cents, raw_description,
    chase_category, chase_type, memo, merchant_id, category_id, category_source,
    category_conf, is_transfer, is_excluded, transfer_pair_id, transfer_candidate,
    category_locked
FROM hold_transactions;

INSERT INTO category_rules (id, pattern, category_id, priority, created_by)
SELECT id, pattern, category_id, priority, created_by FROM hold_category_rules;

INSERT INTO chase_category_mappings (chase_category, category_id)
SELECT chase_category, category_id FROM hold_chase_category_mappings;

INSERT INTO recurring_series (id, merchant_id, cadence_days, typical_cents, last_seen, next_expected, status)
SELECT id, merchant_id, cadence_days, typical_cents, last_seen, next_expected, status
FROM hold_recurring_series;

INSERT INTO anomalies (transaction_id, kind, score, explanation, dismissed)
SELECT transaction_id, kind, score, explanation, dismissed FROM hold_anomalies;

DROP TABLE hold_anomalies;
DROP TABLE hold_recurring_series;
DROP TABLE hold_chase_category_mappings;
DROP TABLE hold_category_rules;
DROP TABLE hold_transactions;
DROP TABLE hold_merchants;
DROP TABLE hold_categories;

CREATE INDEX idx_transactions_date ON transactions (txn_date);
