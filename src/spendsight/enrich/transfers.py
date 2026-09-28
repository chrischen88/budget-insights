"""Transfer detection (SPEC.md §5.3). Pure: rows in, pairs out.

Card payments show up twice: an outflow in checking and an inflow on the card. Both
sides are candidates; a candidate outflow and inflow pair when they are in different
accounts, have equal and opposite amounts, and fall within `window_days` of each other.
Unpaired candidates are reported, never silently excluded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Checking-side descriptions that indicate money moving between the user's own accounts.
TRANSFER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bCHASE CREDIT CRD AUTOPAY\b",
        r"\bPAYMENT TO CHASE CARD ENDING IN \d{4}\b",
        r"\bONLINE TRANSFER (TO|FROM)\b",
    )
)
CARD_PAYMENT_TYPE = "payment"
_ENDING_IN = re.compile(r"\bENDING IN (\d{4})\b", re.IGNORECASE)

DEFAULT_WINDOW_DAYS = 5


@dataclass(frozen=True)
class TransferRow:
    id: str
    account_id: str
    account_kind: str
    account_last4: str | None
    txn_date: date
    amount_cents: int
    raw_description: str
    chase_type: str | None


@dataclass(frozen=True)
class TransferPair:
    outflow_id: str
    inflow_id: str


@dataclass(frozen=True)
class TransferResult:
    pairs: list[TransferPair]
    candidate_ids: set[str]

    @property
    def unpaired_ids(self) -> set[str]:
        paired = {p.outflow_id for p in self.pairs} | {p.inflow_id for p in self.pairs}
        return self.candidate_ids - paired


def is_candidate(row: TransferRow) -> bool:
    if row.amount_cents == 0:
        return False
    if row.account_kind == "credit_card":
        return (row.chase_type or "").strip().lower() == CARD_PAYMENT_TYPE
    return any(p.search(row.raw_description) for p in TRANSFER_PATTERNS)


def _names_other_account(named_by: TransferRow, other: TransferRow) -> bool:
    """False when `named_by` says "ending in NNNN" and `other` is a card with different last4."""
    match = _ENDING_IN.search(named_by.raw_description)
    if match is None or other.account_kind != "credit_card" or other.account_last4 is None:
        return True
    return match.group(1) == other.account_last4


def _day_gap(a: TransferRow, b: TransferRow) -> int:
    return abs((a.txn_date - b.txn_date).days)


def detect_transfers(
    rows: list[TransferRow], *, window_days: int = DEFAULT_WINDOW_DAYS
) -> TransferResult:
    """Pair candidate outflows with candidate inflows, one-to-one, closest dates first."""
    candidates = [r for r in rows if is_candidate(r)]
    outflows = [r for r in candidates if r.amount_cents < 0]
    inflows = [r for r in candidates if r.amount_cents > 0]

    edges = [
        (_day_gap(out, inc), out, inc)
        for out in outflows
        for inc in inflows
        if out.account_id != inc.account_id
        and out.amount_cents == -inc.amount_cents
        and _day_gap(out, inc) <= window_days
        and _names_other_account(out, inc)
        and _names_other_account(inc, out)
    ]
    # Deterministic greedy matching: smallest gap first, ties broken by date then ID.
    edges.sort(key=lambda e: (e[0], e[1].txn_date, e[1].id, e[2].id))

    used: set[str] = set()
    pairs = []
    for _, out, inc in edges:
        if out.id in used or inc.id in used:
            continue
        used.update((out.id, inc.id))
        pairs.append(TransferPair(outflow_id=out.id, inflow_id=inc.id))
    return TransferResult(pairs=pairs, candidate_ids={r.id for r in candidates})
