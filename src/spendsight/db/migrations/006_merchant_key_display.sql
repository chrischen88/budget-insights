-- Merchant display name falls back to the deterministic key (SPEC.md §5.4) before the
-- raw description, so variants of one merchant group together until a clean name exists.
-- Only the merchant expression changes; columns and order match migration 005.

CREATE OR REPLACE VIEW v_transactions AS
SELECT
    t.id                                      AS transaction_id,
    t.txn_date                                AS date,
    t.amount_cents                            AS amount_cents,
    COALESCE(m.clean_name, m.normalized_key, t.raw_description) AS merchant,
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
