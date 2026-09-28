import logging

import pytest

from spendsight.enrich.categorize import (
    CHASE_FALLBACK_CONF,
    RULE_CONF,
    CascadeRow,
    CategoryAssignment,
    MerchantDefault,
    categorize,
    compile_rules,
)

GROCERIES, RESTAURANTS, COFFEE, STREAMING = 10, 20, 30, 40
CHASE = {"Groceries": GROCERIES, "Food & Drink": RESTAURANTS}
RULES = compile_rules([(1, r"\bSTARBUCKS\b", COFFEE, 200), (2, r"NETFLIX", STREAMING, 200)])


def row(
    desc: str = "SYNTH SHOP", *, merchant: int | None = None, chase: str | None = None
) -> CascadeRow:
    return CascadeRow("t1", desc, merchant, chase)


def one(
    r: CascadeRow,
    *,
    rules: list | None = None,  # type: ignore[type-arg]
    defaults: dict[int, MerchantDefault] | None = None,
) -> CategoryAssignment:
    (a,) = categorize(
        [r],
        rules=RULES if rules is None else rules,
        merchant_defaults=defaults or {},
        chase_mapping=CHASE,
    )
    return a


def test_rule_beats_merchant_default_and_chase() -> None:
    a = one(
        row("STARBUCKS STORE 12345", merchant=7, chase="Food & Drink"),
        defaults={7: MerchantDefault(RESTAURANTS, "user", 1.0)},
    )
    assert a == CategoryAssignment("t1", COFFEE, "rule", RULE_CONF)


def test_merchant_default_beats_chase() -> None:
    a = one(
        row(merchant=7, chase="Groceries"), defaults={7: MerchantDefault(RESTAURANTS, "llm", 0.8)}
    )
    assert a == CategoryAssignment("t1", RESTAURANTS, "llm", 0.8)


def test_user_merchant_default_records_user_source() -> None:
    a = one(row(merchant=7), defaults={7: MerchantDefault(RESTAURANTS, "user", 1.0)})
    assert (a.source, a.conf) == ("user", 1.0)


def test_chase_fallback() -> None:
    assert one(row(chase="groceries")) == CategoryAssignment(
        "t1", GROCERIES, "chase", CHASE_FALLBACK_CONF
    )


def test_nothing_matches_clears() -> None:
    assert one(row(chase="Professional Services")) == CategoryAssignment("t1", None, None, None)


def test_rules_are_case_insensitive_and_search_anywhere() -> None:
    assert one(row("recurring card purchase 01/05 netflix.com")).category_id == STREAMING


def test_lower_priority_number_wins_then_lower_id() -> None:
    rules = compile_rules(
        [
            (5, "SYNTH", RESTAURANTS, 200),
            (9, "SYNTH", GROCERIES, 100),  # lower number: runs first
            (3, "SYNTH", COFFEE, 100),  # same priority, lower id: runs first of the two
        ]
    )
    assert one(row("SYNTH SHOP"), rules=rules).category_id == COFFEE


def test_invalid_rule_is_skipped_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        rules = compile_rules([(1, "([unclosed", COFFEE, 100), (2, "SHOP", GROCERIES, 100)])
    assert [r.id for r in rules] == [2]
    assert "rule 1" in caplog.text
    assert one(row("SYNTH SHOP"), rules=rules).category_id == GROCERIES


def test_default_for_other_merchant_is_ignored() -> None:
    a = one(row(merchant=8, chase="Groceries"), defaults={7: MerchantDefault(COFFEE, "user", 1.0)})
    assert a.source == "chase"


def test_every_assignment_has_provenance() -> None:
    rows = [
        CascadeRow("a", "STARBUCKS", None, None),
        CascadeRow("b", "X", 7, None),
        CascadeRow("c", "X", None, "Groceries"),
        CascadeRow("d", "X", None, None),
    ]
    out = categorize(
        rows,
        rules=RULES,
        merchant_defaults={7: MerchantDefault(RESTAURANTS, "user", 1.0)},
        chase_mapping=CHASE,
    )
    for a in out:
        assert (a.category_id is None) == (a.source is None) == (a.conf is None)
    assert [a.source for a in out] == ["rule", "user", "chase", None]


def test_user_default_beats_llm_default_order() -> None:
    llm = one(row(merchant=7, chase="Groceries"), defaults={7: MerchantDefault(COFFEE, "llm", 0.6)})
    assert (llm.category_id, llm.source, llm.conf) == (COFFEE, "llm", 0.6)
    user = one(row(merchant=7), defaults={7: MerchantDefault(RESTAURANTS, "user", 1.0)})
    assert user.source == "user"
    # A rule still wins over either.
    ruled = one(
        row("STARBUCKS", merchant=7), defaults={7: MerchantDefault(RESTAURANTS, "llm", 0.9)}
    )
    assert ruled.source == "rule"
