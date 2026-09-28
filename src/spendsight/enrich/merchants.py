"""Deterministic merchant cleanup (SPEC.md §5.4 step 1). Pure: description in, key out.

The key groups variants of one merchant ("SQ *BLUE BOTTLE COFFEE", "BLUE BOTTLE COFFEE
OAKLAND CA") and is what later steps (merchant lookup, LLM naming) work from. Steps run
in a fixed order; each one is a small regex so failures are easy to trace in tests.
"""

from __future__ import annotations

import re

US_STATES = frozenset(
    [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "DC",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "PR",
    ]
)
# State codes that are also common words/abbreviations ("ENDING IN", "ACME CO") are only
# dropped from fixed-width location fields, never from the end of free text.
_AMBIGUOUS_STATES = frozenset({"IN", "OR", "ME", "OK", "HI", "CO"})
_TRAILING_STATES = US_STATES - _AMBIGUOUS_STATES

# Checking-account wrappers around a card purchase, optionally followed by a MM/DD date.
_CARD_WRAPPER = re.compile(
    r"^(?:RECURRING CARD PURCHASE|CARD PURCHASE WITH PIN|CARD PURCHASE|DEBIT CARD PURCHASE"
    r"|POS DEBIT|POS PURCHASE)\s+(?:\d{2}/\d{2}\s+)?"
)
# Payment processors that prefix the real merchant: "SQ *", "TST*", "PAYPAL *", "SP ".
_PROCESSOR = re.compile(r"^(?:(?:SQ|TST|PAYPAL|SP|PY|DD)\s?\*\s?|SP\s+)")
_ACH_ID = re.compile(r"\b(?:PPD|CCD|WEB|TEL)?\s*ID:\s*\S+")
_PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b")
_DATE = re.compile(r"\b\d{2}/\d{2}(?:/\d{2,4})?\b")
# "*2K4AB1C23": a reference code after an asterisk (must contain a digit).
_STAR_REF = re.compile(r"\*\s*(?=[A-Z]*\d)[A-Z0-9]+")
_BILL_SUFFIX = re.compile(r"/BILL\b")
# A store/terminal number: "#123", "00012345", "F12345".
_STORE_NUMBER = re.compile(r"^#?[A-Z]{0,2}\d{3,}$")
_STORE_WORDS = frozenset({"STORE", "STR", "#"})
# A billing domain after the merchant name: "HELP.UBER.COM", "AMZN.COM", "G.CO/HELPPAY#".
_DOMAIN = re.compile(r"\.(?:COM|NET|ORG|CO)\b")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _drop_fixed_width_location(text: str) -> str:
    """Chase pads descriptors: "NAME<spaces>CITY<spaces>ST". Keep NAME when a state ends it."""
    segments = re.split(r"\s{2,}", text.strip())
    if len(segments) > 1 and segments[-1] in US_STATES:
        return segments[0]
    return text


def _drop_trailing_state(tokens: list[str]) -> list[str]:
    if len(tokens) > 1 and tokens[-1] in _TRAILING_STATES:
        return tokens[:-1]
    return tokens


def _truncate_tokens(tokens: list[str]) -> list[str]:
    """Cut at the first store number or trailing billing domain; never at the first token."""
    for i in range(1, len(tokens)):
        token = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if _STORE_NUMBER.match(token) or _DOMAIN.search(token):
            return tokens[:i]
        if token in _STORE_WORDS and _STORE_NUMBER.match(nxt):
            return tokens[:i]
    return tokens


def normalize_key(raw_description: str) -> str:
    """Deterministic merchant key. Never empty: falls back to the collapsed description."""
    fallback = _collapse(raw_description.upper())
    text = _drop_fixed_width_location(raw_description.upper())
    text = _collapse(text)
    text = _CARD_WRAPPER.sub("", text)
    text = _PROCESSOR.sub("", text)
    for pattern in (_ACH_ID, _PHONE, _DATE, _STAR_REF, _BILL_SUFFIX):
        text = pattern.sub(" ", text)
    tokens = text.replace("*", " ").split()
    # Drop the state before and after truncating, so a key never ends in one and
    # normalizing a stored key again leaves it unchanged.
    tokens = _drop_trailing_state(_truncate_tokens(_drop_trailing_state(tokens)))
    key = " ".join(tokens).strip(" -")
    return key or fallback
