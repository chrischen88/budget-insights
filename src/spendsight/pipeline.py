"""End-to-end processing entry points used by the app (SPEC.md §5)."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from spendsight.enrich.categorize_runner import run_categorization
from spendsight.enrich.merchant_naming import NamingResult, run_merchant_naming
from spendsight.enrich.merchant_runner import run_merchant_linking
from spendsight.enrich.transfer_runner import TransferRunResult, run_transfer_detection
from spendsight.ingest.importer import ImportResult, import_file
from spendsight.llm.client import LLMClient


@dataclass(frozen=True)
class ImportSummary:
    imported: ImportResult
    transfers: TransferRunResult
    merchants_linked: int
    categorized: int
    naming: NamingResult | None = None  # None when LLM features are off


def import_and_process(
    conn: duckdb.DuckDBPyConnection,
    content: bytes,
    *,
    filename: str,
    last4: str,
    display_name: str | None = None,
    llm: LLMClient | None = None,
) -> ImportSummary:
    """Import a file, then run the enrichment stages that exist so far.

    With `llm` set, unnamed merchants are named by the model before categorization;
    without it (local-only / not configured) nothing leaves the machine.
    """
    imported = import_file(conn, content, filename=filename, last4=last4, display_name=display_name)
    transfers = run_transfer_detection(conn)
    merchants_linked = run_merchant_linking(conn)
    naming = run_merchant_naming(conn, llm) if llm is not None else None
    categorized = run_categorization(conn)
    return ImportSummary(
        imported=imported,
        transfers=transfers,
        merchants_linked=merchants_linked,
        categorized=categorized,
        naming=naming,
    )
