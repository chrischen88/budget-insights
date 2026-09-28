-- Categorization cascade (SPEC.md §5.5).
-- category_locked marks a per-transaction user override: the cascade never recomputes it.
-- (category_source alone can't tell an override from a user-set merchant default, which
-- must be recomputed when the default changes.)

ALTER TABLE transactions ADD COLUMN category_locked BOOLEAN DEFAULT FALSE;
UPDATE transactions SET category_locked = TRUE WHERE category_source = 'user';

-- Seed rules: conservative patterns for rows Chase leaves uncategorized (checking) and a
-- few unambiguous brands. Matched case-insensitively against raw_description; lower
-- priority numbers run first. Seeds use 200 so user rules (default 100) win.
INSERT INTO category_rules (pattern, category_id, priority, created_by)
SELECT r.pattern, c.id, 200, 'seed'
FROM (VALUES
    ('\bATM WITHDRAWAL\b',                                    'Cash & ATM'),
    ('\b(NON-CHASE ATM|ATM) FEE\b',                           'ATM Fees'),
    ('\b(MONTHLY SERVICE FEE|SERVICE CHARGE|OVERDRAFT|NSF FEE|INSUFFICIENT FUNDS)\b', 'Bank Fees'),
    ('\bFOREIGN TRANSACTION FEE\b',                           'Bank Fees'),
    ('\b(PURCHASE )?INTEREST CHARGE\b',                       'Interest Charges'),
    ('\b(INTEREST PAYMENT|INTEREST PAID)\b',                  'Interest Income'),
    ('\b(PAYROLL|DIR DEP|DIRECT DEP)\b',                      'Paycheck'),
    ('\b(NETFLIX|HULU|SPOTIFY|DISNEY ?PLUS|HBO ?MAX)\b',      'Streaming Services'),
    ('\bUBER\s*\*?\s*TRIP\b|\bLYFT\b',                        'Rideshare & Taxi'),
    ('\bUBER\s*\*?\s*EATS\b|\bDOORDASH\b|\bGRUBHUB\b',        'Food Delivery'),
    ('\b(STARBUCKS|BLUE BOTTLE|PEET''?S|DUNKIN)\b',           'Coffee Shops')
) AS r(pattern, category_name)
JOIN categories c ON c.name = r.category_name;
