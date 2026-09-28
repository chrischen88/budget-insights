-- One definition of spend vs income (SPEC.md §9, Overview KPIs).
--   flow = 'spend':  any outflow, plus inflows in an expense category (refunds net against spend)
--   flow = 'income': inflows in an income category, or uncategorized inflows
-- so spent + income always nets back to the plain sum of amounts.

CREATE VIEW v_transactions AS
SELECT
    t.id                                      AS transaction_id,
    t.txn_date                                AS date,
    t.amount_cents                            AS amount_cents,
    COALESCE(m.clean_name, t.raw_description) AS merchant,
    c.name                                    AS category,
    COALESCE(p.name, c.name)                  AS parent_category,
    COALESCE(c.is_income, FALSE)              AS is_income,
    a.display_name                            AS account,
    a.kind                                    AS account_kind,
    t.account_id                              AS account_id,
    CASE
        WHEN t.amount_cents < 0 THEN 'spend'
        WHEN c.id IS NOT NULL AND NOT COALESCE(c.is_income, FALSE) THEN 'spend'
        ELSE 'income'
    END                                       AS flow,
    t.is_transfer                             AS is_transfer,
    t.is_excluded                             AS is_excluded
FROM transactions t
JOIN accounts a        ON a.id = t.account_id
LEFT JOIN merchants m  ON m.id = t.merchant_id
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN categories p ON p.id = c.parent_id;

-- Same columns as before (plus account_id, flow); the filter now lives in one place.
CREATE OR REPLACE VIEW v_spend AS
SELECT transaction_id, date, amount_cents, merchant, category, parent_category, is_income,
       account, account_kind, account_id, flow
FROM v_transactions
WHERE NOT is_transfer AND NOT is_excluded;

-- For the "include transfers" toggle: still drops rows the user excluded.
CREATE VIEW v_spend_with_transfers AS
SELECT transaction_id, date, amount_cents, merchant, category, parent_category, is_income,
       account, account_kind, account_id, flow
FROM v_transactions
WHERE NOT is_excluded;
