"""Display helpers: turn domain rows into tables for Streamlit. No SQL, no business rules."""

from __future__ import annotations

from datetime import date

import pandas as pd

from spendsight.db.repository import StoredAnomaly
from spendsight.ingest.importer import ParsedFile
from spendsight.insights.recurring import RecurringCharge
from spendsight.money import format_cents

FORMAT_LABELS = {"chase_card": "Chase credit card", "chase_checking": "Chase checking"}


def date_range_label(parsed: ParsedFile) -> str:
    dates = [t.txn_date for t in parsed.transactions]
    if not dates:
        return "no transactions"
    return f"{min(dates):%b %d, %Y} to {max(dates):%b %d, %Y}"


def preview_frame(parsed: ParsedFile, limit: int = 10) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": [t.txn_date for t in parsed.transactions[:limit]],
            "Description": [t.raw_description for t in parsed.transactions[:limit]],
            "Amount": [format_cents(t.amount_cents) for t in parsed.transactions[:limit]],
            "Chase category": [t.chase_category or "" for t in parsed.transactions[:limit]],
            "Type": [t.chase_type or "" for t in parsed.transactions[:limit]],
        }
    )


def transfer_candidates_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    def amount(row: dict[str, object]) -> str:
        cents = row["amount_cents"]
        return format_cents(cents) if isinstance(cents, int) else ""

    def when(row: dict[str, object]) -> date | None:
        value = row["txn_date"]
        return value if isinstance(value, date) else None

    return pd.DataFrame(
        {
            "Date": [when(r) for r in rows],
            "Account": [str(r["account"]) for r in rows],
            "Description": [str(r["raw_description"]) for r in rows],
            "Amount": [amount(r) for r in rows],
        }
    )


def source_badge(source: str | None, conf: float | None, locked: bool) -> str:
    """Short label for where a category came from (SPEC.md §9 Transactions)."""
    if locked:
        return "Edited by you"
    if source is None:
        return ""
    pct = "" if conf is None else f" {conf:.0%}"
    return {
        "user": "Your merchant default",
        "rule": "Rule",
        "llm": f"AI suggestion{pct}",
        "ml": f"Learned{pct}",
        "chase": f"Chase category{pct}",
    }.get(source, source)


def category_label(category: object, parent: object) -> str:
    if not isinstance(category, str):
        return "Uncategorized"
    return f"{parent} / {category}" if isinstance(parent, str) else category


def transactions_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Search results -> display table. Column order is what the page shows."""
    return pd.DataFrame(
        {
            "Date": frame["date"],
            "Account": frame["account"],
            "Merchant": frame["merchant"],
            "Amount": [format_cents(int(c)) for c in frame["amount_cents"]],
            "Category": [
                category_label(c, p)
                for c, p in zip(frame["category"], frame["parent_category"], strict=True)
            ],
            "Source": [
                source_badge(
                    None if pd.isna(s) else str(s),
                    None if pd.isna(c) else float(c),
                    bool(lk),
                )
                for s, c, lk in zip(
                    frame["category_source"],
                    frame["category_conf"],
                    frame["category_locked"],
                    strict=True,
                )
            ],
            "Description": frame["raw_description"],
        }
    )


def recategorize_message(
    *,
    category: str,
    merchant: str,
    applied_to_merchant: bool,
    merchant_rows: int,
    following: int,
    held_by_override: int,
    held_by_rule: int,
) -> str:
    if not applied_to_merchant:
        return f"Moved this transaction to {category}."
    parts = [f"{following} of {merchant_rows} {merchant} transactions are now {category}."]
    if held_by_rule:
        parts.append(
            f"{held_by_rule} stay where a rule puts them (rules take precedence over "
            "merchant defaults)."
        )
    if held_by_override:
        parts.append(f"{held_by_override} keep a category you set on them individually.")
    return " ".join(parts)


STATUS_LABELS = {
    "active": "Active",
    "lapsed": "Lapsed",
    "dismissed": "Not a subscription",
    "cancelled": "Cancelled",
}


def recurring_table(charges: list[RecurringCharge]) -> pd.DataFrame:
    """Recurring charges -> display table. Costs shown as positive amounts."""
    return pd.DataFrame(
        {
            "Merchant": [c.merchant for c in charges],
            "Cadence": [c.cadence.capitalize() for c in charges],
            "Amount": [format_cents(c.typical_cents, signed=False) for c in charges],
            "Last charge": [c.last_seen for c in charges],
            "Next expected": [c.next_expected for c in charges],
            "Annualized": [format_cents(c.annualized_cents, signed=False) for c in charges],
            "Status": [
                f"Cancelled {c.cancelled_on:%b %d, %Y}"
                if c.cancelled_on is not None and c.status == "cancelled"
                else STATUS_LABELS[c.status]
                for c in charges
            ],
        }
    )


ALERT_LABELS = {
    "amount_outlier": "Unusual amount",
    "duplicate": "Possible duplicate",
    "new_merchant_large": "Large new merchant",
    "price_increase": "Price increase",
}


def alerts_table(alerts: list[StoredAnomaly], *, show_status: bool = False) -> pd.DataFrame:
    """Alerts -> display table, in the order given (newest first)."""
    frame = pd.DataFrame(
        {
            "Date": [a.date for a in alerts],
            "Merchant": [a.merchant for a in alerts],
            "Amount": [format_cents(a.amount_cents) for a in alerts],
            "Alert": [ALERT_LABELS.get(a.kind, a.kind) for a in alerts],
            "Why": [a.explanation for a in alerts],
            "Account": [a.account for a in alerts],
        }
    )
    if show_status:
        frame["Status"] = ["Dismissed" if a.dismissed else "Open" for a in alerts]
    return frame
