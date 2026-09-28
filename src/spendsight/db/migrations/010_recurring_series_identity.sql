-- Recurring detection (SPEC.md §6): at most one series per merchant, so re-detection
-- updates a series in place and keeps the user's dismissal/cancellation.
-- status: 'active' (set by detection) or 'dismissed' (user: not a subscription).
-- Whether an active series has lapsed depends on today's date, so it is derived at read
-- time rather than stored. cancelled_on: the user marked it cancelled on that date.

CREATE UNIQUE INDEX idx_recurring_series_merchant ON recurring_series (merchant_id);
ALTER TABLE recurring_series ADD COLUMN cancelled_on DATE;
