"""Merchant normalization eval (SPEC.md §7.5): category agreement >= 90%.

    uv run python -m evals.run_evals              # live: calls the configured provider ($)
    uv run python -m evals.run_evals --mode oracle  # offline harness check: must score 100%
    uv run python -m evals.run_evals --mode null    # offline harness check: must score 0%

Runs the app's real path: production key cleanup (enrich.merchants.normalize_key), then
llm.merchant_normalize.suggest_merchants through LLMClient with the real prompt, schema,
redaction and adapter. Every rep uses a fresh in-memory database, so the LLM cache never
serves an earlier rep. Failed batches go to errors.jsonl and are never scored as wrong.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from spendsight.config import load_settings
from spendsight.db import repository
from spendsight.db.connection import connect
from spendsight.enrich.merchants import normalize_key
from spendsight.llm.client import LLMClient, build_client
from spendsight.llm.merchant_normalize import BATCH_SIZE, UNKNOWN, suggest_merchants
from spendsight.llm.redact import Redactor
from spendsight.llm.types import LLMError, ProviderResult

EVALS_DIR = Path(__file__).parent
DEFAULT_DATASET = EVALS_DIR / "merchants.jsonl"
TARGET = 0.90


@dataclass(frozen=True)
class Case:
    id: str
    description: str
    expected_name: str
    accept_categories: list[str]

    @property
    def key(self) -> str:
        return normalize_key(self.description)


def load_cases(path: Path = DEFAULT_DATASET) -> list[Case]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            cases.append(
                Case(row["id"], row["description"], row["expected_name"], row["accept_categories"])
            )
    return sorted(cases, key=lambda c: c.id)


# --- grading (pure) -----------------------------------------------------------------------

_NAME_NOISE = re.compile(r"\b(the|inc|llc|co|company|corp|store|stores|usa|us)\b")


def _norm_name(name: str) -> str:
    text = _NAME_NOISE.sub(" ", name.casefold().replace("&", " and "))
    return re.sub(r"[^a-z0-9]", "", text)


def name_matches(predicted: str | None, expected: str) -> bool:
    """Lenient: equal after normalization, or one contains the other ("Uber" / "Uber Eats")."""
    if not predicted:
        return False
    p, e = _norm_name(predicted), _norm_name(expected)
    return bool(p and e) and (p == e or p in e or e in p)


def grade_category(
    predicted: str | None, accept: Sequence[str], parent_of: dict[str, str | None]
) -> str:
    """exact | parent | abstain | wrong. `predicted` None means the model said Unknown (or
    gave a category we reject, which the app treats the same way)."""
    if predicted is None:
        return "exact" if UNKNOWN in accept else "abstain"
    if predicted in accept:
        return "exact"
    if any(parent_of.get(a) == predicted for a in accept):
        return "parent"
    return "wrong"


def majority_baseline(cases: Sequence[Case]) -> tuple[str, float]:
    """Best constant answer's score: a floor the model must clearly beat."""
    counts = Counter(cat for c in cases for cat in set(c.accept_categories))
    best, hits = counts.most_common(1)[0]
    return best, hits / len(cases)


# --- offline adapters for harness self-checks -----------------------------------------------


class _OfflineAdapter:
    """Answers from the answer key (oracle) or with Unknown (null). Never touches a network."""

    def __init__(self, name: str, answer: Callable[[str], tuple[str, str]]) -> None:
        self.name = name
        self._answer = answer

    def complete_json(self, *, user: str, **_: Any) -> ProviderResult:
        merchants = json.loads(user)["merchants"]
        items = []
        for m in merchants:
            clean_name, category = self._answer(m["description"])
            items.append(
                {"id": m["id"], "clean_name": clean_name, "category": category, "confidence": 1.0}
            )
        return ProviderResult(json.dumps({"merchants": items}), 0, 0)


def offline_client(mode: str, cases: Sequence[Case], conn: duckdb.DuckDBPyConnection) -> LLMClient:
    redactor = Redactor()
    by_sent = {redactor.redact(c.key): c for c in cases}

    def oracle(description: str) -> tuple[str, str]:
        case = by_sent[description]
        return case.expected_name, case.accept_categories[0]

    def null(description: str) -> tuple[str, str]:
        return description.title(), UNKNOWN

    adapter = _OfflineAdapter(mode, oracle if mode == "oracle" else null)
    return LLMClient(conn, adapter, f"offline-{mode}", redactor)


# --- running ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    case_id: str
    rep: int
    sent_key: str
    predicted_name: str | None
    predicted_category: str | None
    confidence: float | None
    grade: str
    name_ok: bool
    name_is_hard: bool  # title-casing the key alone would not produce the expected name


def run_rep(
    rep: int, cases: Sequence[Case], client: LLMClient, conn: duckdb.DuckDBPyConnection
) -> tuple[list[Row], list[dict[str, Any]]]:
    categories = {
        c.name: c.id for c in repository.list_categories(conn) if c.name != "Uncategorized"
    }
    names_by_id = {v: k for k, v in categories.items()}
    parent_of = {c.name: c.parent_name for c in repository.list_categories(conn)}
    rows: list[Row] = []
    errors: list[dict[str, Any]] = []
    for start in range(0, len(cases), BATCH_SIZE):
        batch = list(cases[start : start + BATCH_SIZE])
        try:
            suggestions = suggest_merchants(client, [c.key for c in batch], categories)
        except LLMError as exc:
            errors.extend({"case_id": c.id, "rep": rep, "error": str(exc)[:200]} for c in batch)
            continue
        by_key = {s.key: s for s in suggestions}
        for case in batch:
            s = by_key.get(case.key)
            if s is None:
                errors.append({"case_id": case.id, "rep": rep, "error": "missing from response"})
                continue
            predicted = names_by_id.get(s.category_id) if s.category_id is not None else None
            rows.append(
                Row(
                    case_id=case.id,
                    rep=rep,
                    sent_key=case.key,
                    predicted_name=s.clean_name,
                    predicted_category=predicted,
                    confidence=s.confidence,
                    grade=grade_category(predicted, case.accept_categories, parent_of),
                    name_ok=name_matches(s.clean_name, case.expected_name),
                    name_is_hard=not name_matches(case.key.title(), case.expected_name),
                )
            )
    return rows, errors


def summarize(rows: Sequence[Row], errors: Sequence[dict[str, Any]], reps: int) -> dict[str, Any]:
    per_rep = []
    for rep in range(reps):
        rr = [r for r in rows if r.rep == rep]
        n = len(rr) or 1
        grades = Counter(r.grade for r in rr)
        hard = [r for r in rr if r.name_is_hard]
        per_rep.append(
            {
                "rep": rep,
                "scored": len(rr),
                "agreement": (grades["exact"] + grades["parent"]) / n,
                "exact": grades["exact"] / n,
                "parent_only": grades["parent"] / n,
                "abstain": grades["abstain"] / n,
                "wrong": grades["wrong"] / n,
                "name_match": sum(r.name_ok for r in rr) / n,
                # Only names the model had to work out; the easy ones inflate name_match.
                "hard_name_match": sum(r.name_ok for r in hard) / (len(hard) or 1),
                "hard_names": len(hard),
            }
        )
    agreements = [p["agreement"] for p in per_rep]
    return {
        "reps": per_rep,
        "agreement_mean": statistics.fmean(agreements),
        "agreement_min": min(agreements),
        "agreement_max": max(agreements),
        "errors": len(errors),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--mode", choices=["live", "oracle", "null"], default="live")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = parser.parse_args(argv)

    cases = load_cases(args.dataset)
    settings = load_settings()
    if args.mode == "live":
        if not settings.llm_enabled:
            print("LLM is not configured (or SPENDSIGHT_LOCAL_ONLY=true). Nothing sent.")
            return 2
        calls = -(-len(cases) // BATCH_SIZE) * args.reps
        print(
            f"About to send {len(cases)} synthetic merchant descriptions x {args.reps} rep(s) "
            f"= {calls} request(s) to {settings.llm_provider} / {settings.llm_model}."
        )
        if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
            return 2

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or EVALS_DIR / "results" / f"{stamp}-{args.mode}"
    out.mkdir(parents=True, exist_ok=True)

    rows: list[Row] = []
    errors: list[dict[str, Any]] = []
    tokens = Counter[str]()
    for rep in range(args.reps):
        conn = connect(":memory:")  # fresh cache per rep
        client = (
            build_client(settings, conn)
            if args.mode == "live"
            else offline_client(args.mode, cases, conn)
        )
        assert client is not None
        rep_rows, rep_errors = run_rep(rep, cases, client, conn)
        rows += rep_rows
        errors += rep_errors
        usage = conn.execute(
            "SELECT COALESCE(sum(input_tokens), 0), COALESCE(sum(output_tokens), 0) FROM llm_calls"
        ).fetchone() or (0, 0)
        tokens["input"] += int(usage[0])
        tokens["output"] += int(usage[1])
        conn.close()

    summary = summarize(rows, errors, args.reps)
    baseline_cat, baseline = majority_baseline(cases)
    summary.update(
        {
            "mode": args.mode,
            "provider": settings.llm_provider if args.mode == "live" else args.mode,
            "model": settings.llm_model if args.mode == "live" else None,
            "cases": len(cases),
            "target": TARGET,
            "majority_baseline": {"category": baseline_cat, "agreement": baseline},
            "tokens": dict(tokens),
        }
    )
    with (out / "results.jsonl").open("w") as f:
        f.writelines(json.dumps(asdict(r)) + "\n" for r in rows)
    with (out / "errors.jsonl").open("w") as f:
        f.writelines(json.dumps(e) + "\n" for e in errors)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))

    print(
        f"Category agreement: {summary['agreement_mean']:.1%} "
        f"(min {summary['agreement_min']:.1%}, max {summary['agreement_max']:.1%}, "
        f"{args.reps} rep(s)); target {TARGET:.0%}"
    )
    rep0 = summary["reps"][0]
    print(
        f"  exact {rep0['exact']:.1%} · parent-only {rep0['parent_only']:.1%} · "
        f"abstained {rep0['abstain']:.1%} · wrong {rep0['wrong']:.1%} · "
        f"names {rep0['name_match']:.1%} "
        f"(hard names {rep0['hard_name_match']:.1%} of {rep0['hard_names']})"
    )
    print(f"  majority baseline ('{baseline_cat}'): {baseline:.1%} · errors: {len(errors)}")
    print(f"  tokens: {tokens['input']} in / {tokens['output']} out · results: {out}")
    wrong = [r for r in rows if r.grade in {"wrong", "abstain"} and r.rep == 0]
    for r in wrong[:15]:
        print(f"  {r.grade:>7}  {r.case_id}  {r.sent_key!r} -> {r.predicted_category}")
    if args.mode == "live":
        return 0 if summary["agreement_mean"] >= TARGET and not errors else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
