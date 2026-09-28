"""Redaction applied to every outbound LLM payload (SPEC.md §8)."""

from __future__ import annotations

import re
from collections.abc import Iterable

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b")
_LONG_DIGITS = re.compile(r"\d{6,}")

EMAIL, PHONE, NUMBER, NAME = "[EMAIL]", "[PHONE]", "[NUMBER]", "[NAME]"
PLACEHOLDERS = (EMAIL, PHONE, NUMBER, NAME)


class Redactor:
    """Masks emails, phone numbers, digit runs of 6+ and a configured list of names."""

    def __init__(self, names: Iterable[str] = ()) -> None:
        cleaned = sorted({n.strip() for n in names if n.strip()}, key=len, reverse=True)
        self._names = (
            re.compile(r"\b(?:" + "|".join(re.escape(n) for n in cleaned) + r")\b", re.IGNORECASE)
            if cleaned
            else None
        )

    def redact(self, text: str) -> str:
        text = _EMAIL.sub(EMAIL, text)
        text = _PHONE.sub(PHONE, text)
        text = _LONG_DIGITS.sub(NUMBER, text)
        if self._names is not None:
            text = self._names.sub(NAME, text)
        return text


def contains_placeholder(text: str) -> bool:
    return any(p in text for p in PLACEHOLDERS)
