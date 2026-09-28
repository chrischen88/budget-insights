import pytest

from spendsight.llm.redact import Redactor, contains_placeholder


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("VENMO PAYMENT 1000000000", "VENMO PAYMENT [PHONE]"),  # 10 digits look like a phone
        ("REF 1234567", "REF [NUMBER]"),
        ("REF 123456", "REF [NUMBER]"),
        ("STORE 12345", "STORE 12345"),  # 5 digits kept
        ("CALL 866-555-0100", "CALL [PHONE]"),
        ("CALL (866) 555-0100", "CALL [PHONE]"),
        ("jane.sample+bills@example.com PAID", "[EMAIL] PAID"),
        ("SYNTH COFFEE CO", "SYNTH COFFEE CO"),
    ],
)
def test_default_redaction(text: str, expected: str) -> None:
    assert Redactor().redact(text) == expected


def test_names_are_masked_case_insensitively_as_whole_words() -> None:
    r = Redactor(["Jane Sample", "Bob"])
    assert r.redact("ZELLE PAYMENT TO JANE SAMPLE") == "ZELLE PAYMENT TO [NAME]"
    assert r.redact("bob's diner") == "[NAME]'s diner"
    assert r.redact("BOBCAT RENTALS") == "BOBCAT RENTALS"  # not a whole word


def test_longer_names_win_over_substrings() -> None:
    assert Redactor(["Jane", "Jane Sample"]).redact("TO JANE SAMPLE") == "TO [NAME]"


def test_blank_names_ignored() -> None:
    assert Redactor(["", "  "]).redact("SYNTH SHOP") == "SYNTH SHOP"


def test_redaction_is_idempotent() -> None:
    r = Redactor(["Jane Sample"])
    once = r.redact("jane sample 1234567 a@b.com 866-555-0100")
    assert r.redact(once) == once


def test_contains_placeholder() -> None:
    assert contains_placeholder("Payment [NUMBER]")
    assert not contains_placeholder("Blue Bottle Coffee")
