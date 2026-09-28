"""Name unknown merchants with the LLM (SPEC.md §5.4 step 3).

Only merchants nobody has named are sent. Peer-to-peer payments (Zelle, Venmo, ...) are
never sent: their keys carry people's names and say nothing about what the money was for.
Merchants that only appear on transfers (card payments, own-account moves) are skipped too.
Any failure leaves the remaining merchants unnamed for the next run; imports never fail.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import duckdb

from spendsight.db import repository
from spendsight.llm.client import LLMClient
from spendsight.llm.merchant_normalize import BATCH_SIZE, suggest_merchants
from spendsight.llm.types import LLMError

log = logging.getLogger(__name__)

P2P_PATTERN = re.compile(
    r"\b(ZELLE|VENMO|CASH ?APP|SQUARE CASH|APPLE CASH|PAYPAL (TRANSFER|INST XFER))\b",
    re.IGNORECASE,
)


def is_p2p(key: str) -> bool:
    return P2P_PATTERN.search(key) is not None


@dataclass(frozen=True)
class NamingResult:
    sent: int = 0  # merchants sent to the LLM
    named: int = 0  # merchants updated from the response
    skipped_p2p: int = 0
    failed: bool = False


def run_merchant_naming(conn: duckdb.DuckDBPyConnection, client: LLMClient) -> NamingResult:
    pending = repository.merchants_needing_names(conn)
    candidates = [(mid, key) for mid, key in pending if not is_p2p(key)]
    skipped = len(pending) - len(candidates)
    if not candidates:
        return NamingResult(skipped_p2p=skipped)

    categories = {
        c.name: c.id for c in repository.list_categories(conn) if c.name != "Uncategorized"
    }
    sent = named = 0
    for start in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[start : start + BATCH_SIZE]
        ids_by_key = {key: mid for mid, key in batch}
        try:
            suggestions = suggest_merchants(client, [key for _, key in batch], categories)
        except LLMError as exc:
            log.warning("merchant naming stopped: %s", exc)
            return NamingResult(sent + len(batch), named, skipped, failed=True)
        sent += len(batch)
        rows = [
            (ids_by_key[s.key], s.clean_name, s.category_id, s.confidence)
            for s in suggestions
            if s.key in ids_by_key
        ]
        conn.execute("BEGIN TRANSACTION")
        try:
            named += repository.save_llm_merchant_names(conn, rows)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return NamingResult(sent, named, skipped)
