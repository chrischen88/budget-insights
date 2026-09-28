"""The merchant eval's dataset and harness, checked offline (no network, no cost)."""

from pathlib import Path

import pytest
from evals.run_evals import (
    grade_category,
    load_cases,
    main,
    majority_baseline,
    name_matches,
)

from spendsight.db.connection import connect
from spendsight.db.repository import list_categories
from spendsight.llm.client import load_prompt
from spendsight.llm.merchant_normalize import PROMPT_NAME, PROMPT_VERSION, UNKNOWN

CASES = load_cases()


def test_dataset_size_and_ids() -> None:
    assert len(CASES) >= 200
    assert len({c.id for c in CASES}) == len(CASES)


def test_keys_are_unique_so_answers_map_back() -> None:
    keys = [c.key for c in CASES]
    assert len(set(keys)) == len(keys)


def test_every_accepted_category_exists() -> None:
    conn = connect(":memory:")
    names = {c.name for c in list_categories(conn)} | {UNKNOWN}
    for case in CASES:
        assert set(case.accept_categories) <= names, case.id


def test_no_prompt_examples_leak_into_the_dataset() -> None:
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION).text
    for case in CASES:
        assert case.key not in prompt, case.id


def test_both_directions_covered() -> None:
    abstain = [c for c in CASES if c.accept_categories == [UNKNOWN]]
    assert 3 <= len(abstain) < len(CASES) // 10


def test_majority_baseline_is_far_below_target() -> None:
    _, score = majority_baseline(CASES)
    assert score < 0.2


@pytest.mark.parametrize(
    ("predicted", "accept", "expected"),
    [
        ("Coffee Shops", ["Coffee Shops"], "exact"),
        ("Fast Food", ["Coffee Shops", "Fast Food"], "exact"),
        ("Food & Dining", ["Coffee Shops"], "parent"),
        ("Groceries", ["Coffee Shops"], "wrong"),
        (None, ["Coffee Shops"], "abstain"),
        (None, [UNKNOWN], "exact"),
        ("Rent & Mortgage", [UNKNOWN], "wrong"),
    ],
)
def test_grade_category(predicted: str | None, accept: list[str], expected: str) -> None:
    parent_of = {"Coffee Shops": "Food & Dining", "Fast Food": "Food & Dining"}
    assert grade_category(predicted, accept, parent_of) == expected


@pytest.mark.parametrize(
    ("predicted", "expected", "ok"),
    [
        ("Starbucks", "Starbucks", True),
        ("The Home Depot", "Home Depot", True),
        ("Peet's Coffee", "Peet's Coffee", True),
        ("Uber Eats", "Uber Eats", True),
        ("Trader Joe's", "Trader Joe S", True),  # punctuation is ignored
        ("Walmart", "Target", False),
        (None, "Target", False),
        ("", "Target", False),
    ],
)
def test_name_matches(predicted: str | None, expected: str, ok: bool) -> None:
    assert name_matches(predicted, expected) is ok


def _summary(out: Path) -> dict[str, object]:
    import json

    return json.loads((out / "summary.json").read_text())  # type: ignore[no-any-return]


def test_oracle_scores_perfectly(tmp_path: Path) -> None:
    assert main(["--mode", "oracle", "--out", str(tmp_path)]) == 0
    summary = _summary(tmp_path)
    assert summary["agreement_mean"] == 1.0
    assert summary["errors"] == 0


def test_null_only_gets_the_abstain_cases(tmp_path: Path) -> None:
    main(["--mode", "null", "--out", str(tmp_path)])
    abstain_cases = sum(1 for c in CASES if UNKNOWN in c.accept_categories)
    assert _summary(tmp_path)["agreement_mean"] == pytest.approx(abstain_cases / len(CASES))


def test_live_mode_refuses_when_llm_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPENDSIGHT_LOCAL_ONLY", "true")
    assert main(["--mode", "live", "--out", str(tmp_path), "--yes"]) == 2
    assert not (tmp_path / "summary.json").exists()
