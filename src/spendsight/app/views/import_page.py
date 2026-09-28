"""Import Chase CSV exports (SPEC.md §9): detect, preview, import, summarize, review."""

from __future__ import annotations

import re

import streamlit as st

from spendsight.app.formatting import (
    FORMAT_LABELS,
    date_range_label,
    preview_frame,
    transfer_candidates_frame,
)
from spendsight.app.state import get_connection, get_settings
from spendsight.db import repository
from spendsight.ingest.importer import ParsedFile, guess_last4, parse_file
from spendsight.ingest.models import IngestError
from spendsight.llm.client import build_client
from spendsight.pipeline import ImportSummary, import_and_process

LAST4 = re.compile(r"[0-9]{4}")

conn = get_connection()
llm = build_client(get_settings(), conn)  # None when local-only or not configured

st.title("Import")
st.caption(
    "Upload Chase checking or credit card CSV exports. Re-importing a file, or one that "
    "overlaps earlier dates, never creates duplicates."
)

uploads = st.file_uploader("Chase CSV exports", type=["csv"], accept_multiple_files=True)

ready: list[tuple[str, bytes, str]] = []  # (filename, content, last4)
for i, upload in enumerate(uploads or []):
    content = upload.getvalue()
    st.subheader(upload.name)
    try:
        parsed: ParsedFile = parse_file(content)
    except IngestError as exc:
        st.error(f"Can't import this file: {exc}")
        continue
    st.caption(
        f"{FORMAT_LABELS[parsed.format]} · {len(parsed.transactions)} transactions · "
        f"{date_range_label(parsed)}"
    )
    last4 = st.text_input(
        "Account last 4 digits",
        value=guess_last4(upload.name) or "",
        max_chars=4,
        key=f"last4-{i}-{upload.name}",
        help="Only the last 4 digits are stored. Suggested from the filename when possible.",
    ).strip()
    if not LAST4.fullmatch(last4):
        st.warning("Enter the account's last 4 digits to import this file.")
    else:
        ready.append((upload.name, content, last4))
    with st.expander("Preview first 10 rows"):
        st.dataframe(preview_frame(parsed), hide_index=True)

if uploads:
    clicked = st.button(
        f"Import {len(ready)} file(s)", type="primary", disabled=not ready, key="import"
    )
    if clicked:
        results: list[tuple[str, ImportSummary]] = []
        for filename, content, last4 in ready:
            try:
                with st.spinner(f"Importing {filename}..."):
                    summary = import_and_process(
                        conn, content, filename=filename, last4=last4, llm=llm
                    )
                results.append((filename, summary))
            except IngestError as exc:
                st.error(f"{filename}: {exc}")
        if results:
            st.subheader("Import summary")
            cols = st.columns(4)
            cols[0].metric("New transactions", sum(r.imported.new_rows for _, r in results))
            cols[1].metric("Duplicates skipped", sum(r.imported.duplicate_rows for _, r in results))
            cols[2].metric("Transfers found", sum(r.transfers.new_pairs for _, r in results))
            cols[3].metric("Categorized", sum(r.categorized for _, r in results))
            namings = [r.naming for _, r in results if r.naming is not None]
            if namings:
                st.caption(
                    f"{sum(n.named for n in namings)} merchant(s) named by AI "
                    f"({llm.provider_name if llm else ''}); "
                    f"{sum(n.skipped_p2p for n in namings)} person-to-person payment(s) kept "
                    "local."
                )
                if any(n.failed for n in namings):
                    st.warning(
                        "AI merchant naming didn't finish. Imported data is fine; the "
                        "remaining merchants will be named on the next import."
                    )
            elif llm is None:
                st.caption("AI merchant naming is off (local-only mode or no API key/model).")
            for filename, r in results:
                if r.imported.already_imported:
                    st.info(f"{filename}: this exact file was already imported; nothing changed.")

st.divider()
st.subheader("Transfers to review")
candidates = repository.unpaired_transfer_candidates(conn)
if not candidates:
    st.success("No unpaired transfers to review.")
else:
    st.warning(
        f"{len(candidates)} transaction(s) look like transfers but have no matching side. "
        "They still count toward income and spending until you confirm them. Importing the "
        "other account's file usually pairs them automatically."
    )
    selection = st.dataframe(
        transfer_candidates_frame(candidates),
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="transfer-candidates",
    )
    chosen = [str(candidates[i]["id"]) for i in selection.selection.rows]
    if st.button(
        "Mark selected as transfers", disabled=not chosen, key="mark-transfers"
    ) and repository.mark_as_transfer(conn, chosen):
        st.rerun()
