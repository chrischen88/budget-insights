-- Initial schema (SPEC.md §4). Money is BIGINT cents; negative = outflow.

CREATE SEQUENCE seq_categories START 1;
CREATE SEQUENCE seq_merchants START 1;
CREATE SEQUENCE seq_category_rules START 1;
CREATE SEQUENCE seq_recurring_series START 1;

CREATE TABLE accounts (
    id           TEXT PRIMARY KEY,          -- e.g. 'chase-card-1234'
    kind         TEXT NOT NULL CHECK (kind IN ('credit_card', 'checking')),
    display_name TEXT NOT NULL,
    last4        TEXT CHECK (last4 IS NULL OR regexp_full_match(last4, '[0-9]{4}'))
);

CREATE TABLE imports (
    id          TEXT PRIMARY KEY,           -- uuid
    account_id  TEXT REFERENCES accounts(id),
    filename    TEXT,
    file_sha256 TEXT UNIQUE,                -- blocks re-importing an identical file
    imported_at TIMESTAMP,
    row_count   INTEGER
);

CREATE TABLE categories (
    id        INTEGER PRIMARY KEY DEFAULT nextval('seq_categories'),
    name      TEXT UNIQUE NOT NULL,
    parent_id INTEGER REFERENCES categories(id),
    is_income BOOLEAN DEFAULT FALSE
);

CREATE TABLE merchants (
    id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_merchants'),
    normalized_key      TEXT UNIQUE,
    clean_name          TEXT,
    default_category_id INTEGER REFERENCES categories(id),
    source              TEXT CHECK (source IS NULL OR source IN ('llm', 'user')),
    confidence          DOUBLE
);

CREATE TABLE transactions (
    id              TEXT PRIMARY KEY,       -- deterministic hash, SPEC.md §5.2
    account_id      TEXT REFERENCES accounts(id),
    import_id       TEXT REFERENCES imports(id),
    txn_date        DATE NOT NULL,
    post_date       DATE,
    amount_cents    BIGINT NOT NULL,
    raw_description TEXT NOT NULL,
    chase_category  TEXT,
    chase_type      TEXT,
    memo            TEXT,
    merchant_id     INTEGER REFERENCES merchants(id),
    category_id     INTEGER REFERENCES categories(id),
    category_source TEXT CHECK (category_source IS NULL OR category_source IN ('rule', 'user', 'ml', 'llm', 'chase')),
    category_conf   DOUBLE,
    is_transfer     BOOLEAN DEFAULT FALSE,
    is_excluded     BOOLEAN DEFAULT FALSE,
    -- A category is never written without its provenance.
    CHECK (category_id IS NULL OR (category_source IS NOT NULL AND category_conf IS NOT NULL))
);

CREATE TABLE category_rules (
    id          INTEGER PRIMARY KEY DEFAULT nextval('seq_category_rules'),
    pattern     TEXT NOT NULL,              -- regex on raw_description
    category_id INTEGER REFERENCES categories(id),
    priority    INTEGER DEFAULT 100,
    created_by  TEXT CHECK (created_by IS NULL OR created_by IN ('seed', 'user'))
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

CREATE TABLE llm_cache (
    prompt_hash TEXT PRIMARY KEY,           -- sha256(model + prompt_version + input)
    response    JSON,
    created_at  TIMESTAMP
);

CREATE INDEX idx_transactions_date ON transactions (txn_date);

-- Everything that counts toward spend/income: transfers and excluded rows removed.
-- Totals must come from this view rather than re-implementing the filter.
CREATE VIEW v_spend AS
SELECT
    t.id                                   AS transaction_id,
    t.txn_date                             AS date,
    t.amount_cents                         AS amount_cents,
    COALESCE(m.clean_name, t.raw_description) AS merchant,
    c.name                                 AS category,
    COALESCE(p.name, c.name)               AS parent_category,
    COALESCE(c.is_income, FALSE)           AS is_income,
    a.display_name                         AS account,
    a.kind                                 AS account_kind
FROM transactions t
JOIN accounts a        ON a.id = t.account_id
LEFT JOIN merchants m  ON m.id = t.merchant_id
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN categories p ON p.id = c.parent_id
WHERE NOT t.is_transfer
  AND NOT t.is_excluded;
