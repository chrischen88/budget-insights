-- Transfer pairing (SPEC.md §5.3).
-- transfer_candidate: the row looks like a transfer (card payment, autopay, own-account move).
-- transfer_pair_id:   the matched opposite side. Paired rows also get is_transfer = TRUE.
-- A candidate with no pair keeps is_transfer = FALSE (still counted) and awaits review.

ALTER TABLE transactions ADD COLUMN transfer_pair_id TEXT;
ALTER TABLE transactions ADD COLUMN transfer_candidate BOOLEAN DEFAULT FALSE;
