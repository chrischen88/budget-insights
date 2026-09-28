-- ML output tables hold plain references, and anomalies get an identity.
--
-- DuckDB (1.5) rejects `UPDATE ... RETURNING` on any row another table references by
-- foreign key, even when the key itself is unchanged. The repository updates
-- transactions (recategorize) and merchants ("apply to merchant", LLM naming) that way,
-- so once recurring_series or anomalies pointed at a row, editing it failed. These two
-- tables are derived output, rebuilt after every import, so their references become plain
-- columns checked by repository.integrity_problems() (SPEC.md §4).
--
-- Also: at most one anomaly per (transaction, kind), so re-detection updates rows in place
-- and keeps the user's dismissal.

CREATE TABLE hold_recurring_series AS SELECT * FROM recurring_series;
CREATE TABLE hold_anomalies AS SELECT * FROM anomalies;
DROP TABLE recurring_series;
DROP TABLE anomalies;

CREATE TABLE recurring_series (
    id            INTEGER PRIMARY KEY DEFAULT nextval('seq_recurring_series'),
    merchant_id   INTEGER NOT NULL,         -- merchants.id; not a FK (see header)
    cadence_days  INTEGER,
    typical_cents BIGINT,
    last_seen     DATE,
    next_expected DATE,
    status        TEXT CHECK (status IS NULL OR status IN ('active', 'lapsed', 'dismissed')),
    cancelled_on  DATE
);
CREATE UNIQUE INDEX idx_recurring_series_merchant ON recurring_series (merchant_id);

CREATE TABLE anomalies (
    transaction_id TEXT NOT NULL,           -- transactions.id; not a FK (see header)
    kind           TEXT NOT NULL CHECK (kind IN ('amount_outlier', 'duplicate', 'new_merchant_large', 'price_increase')),
    score          DOUBLE,
    explanation    TEXT,
    dismissed      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE UNIQUE INDEX idx_anomalies_transaction_kind ON anomalies (transaction_id, kind);

INSERT INTO recurring_series
    (id, merchant_id, cadence_days, typical_cents, last_seen, next_expected, status, cancelled_on)
SELECT id, merchant_id, cadence_days, typical_cents, last_seen, next_expected, status, cancelled_on
FROM hold_recurring_series;

-- Nothing wrote anomalies before this migration; DISTINCT ON guards the new identity.
INSERT INTO anomalies (transaction_id, kind, score, explanation, dismissed)
SELECT DISTINCT ON (transaction_id, kind)
    transaction_id, kind, score, explanation, COALESCE(dismissed, FALSE)
FROM hold_anomalies
WHERE transaction_id IS NOT NULL AND kind IS NOT NULL;

DROP TABLE hold_recurring_series;
DROP TABLE hold_anomalies;
