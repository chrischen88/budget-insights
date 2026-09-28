"""Categorization cascade (SPEC.md §5.5). Pure: rows + lookup tables in, assignments out.

First match wins:
  1. user override       -> locked rows are never passed in
  2. rule                -> category_rules, lowest priority number first, then rule id
  3. merchant default    -> merchants.default_category_id set by the user
  4. ML classifier       -> predictions passed in, used at or above the threshold
  5. LLM suggestion      -> merchants.default_category_id set by the LLM
  6. Chase fallback      -> chase_category_mappings
A row nothing matches gets a clearing assignment, so stale categories don't linger.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

CategorySource = Literal["rule", "user", "ml", "llm", "chase"]

RULE_CONF = 1.0
USER_MERCHANT_CONF = 1.0
# Chase's categories are coarse and sometimes wrong, so this is the least trusted source.
CHASE_FALLBACK_CONF = 0.5
DEFAULT_ML_THRESHOLD = 0.75  # SPEC.md §5.5; configurable as SPENDSIGHT_ML_CONF_THRESHOLD


@dataclass(frozen=True)
class CategoryAssignment:
    transaction_id: str
    category_id: int | None  # None clears a previous assignment
    source: CategorySource | None
    conf: float | None


@dataclass(frozen=True)
class Rule:
    id: int
    pattern: re.Pattern[str]
    category_id: int
    priority: int


@dataclass(frozen=True)
class MerchantDefault:
    category_id: int
    source: Literal["user", "llm"]
    conf: float


@dataclass(frozen=True)
class MlSuggestion:
    category_id: int
    conf: float


@dataclass(frozen=True)
class CascadeRow:
    transaction_id: str
    raw_description: str
    merchant_id: int | None
    chase_category: str | None


def compile_rules(raw: Iterable[tuple[int, str, int, int]]) -> list[Rule]:
    """(id, pattern, category_id, priority) -> rules in evaluation order.

    Patterns match case-insensitively anywhere in raw_description. An invalid pattern is
    skipped (and logged by id only) rather than failing the whole import.
    """
    rules = []
    for rule_id, pattern, category_id, priority in raw:
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error:
            log.warning("skipping category rule %s: invalid pattern", rule_id)
            continue
        rules.append(Rule(rule_id, compiled, category_id, priority))
    return sorted(rules, key=lambda r: (r.priority, r.id))


def normalize_chase_category(raw: str) -> str:
    return " ".join(raw.split()).casefold()


def categorize(
    rows: Iterable[CascadeRow],
    *,
    rules: list[Rule],
    merchant_defaults: Mapping[int, MerchantDefault],
    chase_mapping: Mapping[str, int],
    ml_suggestions: Mapping[str, MlSuggestion] | None = None,
    ml_threshold: float = DEFAULT_ML_THRESHOLD,
) -> list[CategoryAssignment]:
    """`ml_suggestions` maps transaction id -> the classifier's best guess; only guesses
    with conf >= ml_threshold are used."""
    chase_lookup = {normalize_chase_category(k): v for k, v in chase_mapping.items()}
    confident = {txn: s for txn, s in (ml_suggestions or {}).items() if s.conf >= ml_threshold}
    out = []
    for row in rows:
        out.append(_categorize_one(row, rules, merchant_defaults, chase_lookup, confident))
    return out


def _categorize_one(
    row: CascadeRow,
    rules: list[Rule],
    merchant_defaults: Mapping[int, MerchantDefault],
    chase_lookup: Mapping[str, int],
    ml_suggestions: Mapping[str, MlSuggestion],
) -> CategoryAssignment:
    txn = row.transaction_id
    for rule in rules:
        if rule.pattern.search(row.raw_description):
            return CategoryAssignment(txn, rule.category_id, "rule", RULE_CONF)
    default = merchant_defaults.get(row.merchant_id) if row.merchant_id is not None else None
    if default is not None and default.source == "user":
        return CategoryAssignment(txn, default.category_id, "user", default.conf)
    suggestion = ml_suggestions.get(txn)
    if suggestion is not None:
        return CategoryAssignment(txn, suggestion.category_id, "ml", suggestion.conf)
    if default is not None and default.source == "llm":
        return CategoryAssignment(txn, default.category_id, "llm", default.conf)
    chase_id = chase_lookup.get(normalize_chase_category(row.chase_category or ""))
    if chase_id is not None:
        return CategoryAssignment(txn, chase_id, "chase", CHASE_FALLBACK_CONF)
    return CategoryAssignment(txn, None, None, None)
