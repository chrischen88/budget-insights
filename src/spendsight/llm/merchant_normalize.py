"""LLM merchant normalization (SPEC.md §7.1).

Sends cleaned merchant keys (never amounts, dates or account data) with our category
names; gets back a readable name, a category and a confidence per merchant. Anything
invalid (unknown id, category not in our list, masked placeholder in a name) is dropped
here so it can never be stored.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from spendsight.llm.client import LLMClient, load_prompt
from spendsight.llm.redact import contains_placeholder

PROMPT_NAME, PROMPT_VERSION = "merchant_normalize", 1
BATCH_SIZE = 50
UNKNOWN = "Unknown"
MAX_NAME_LENGTH = 80


class _Item(BaseModel):
    id: int
    clean_name: str
    category: str
    confidence: float = Field(ge=0.0, le=1.0)


class _Response(BaseModel):
    merchants: list[_Item]


@dataclass(frozen=True)
class MerchantSuggestion:
    key: str
    clean_name: str | None
    category_id: int | None
    confidence: float | None


def response_schema(category_names: Sequence[str]) -> dict[str, Any]:
    """Strict JSON schema, valid for both providers' structured-output modes."""
    return {
        "type": "object",
        "properties": {
            "merchants": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "clean_name": {"type": "string"},
                        "category": {"type": "string", "enum": [*category_names, UNKNOWN]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["id", "clean_name", "category", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["merchants"],
        "additionalProperties": False,
    }


def user_content(keys: Sequence[str], category_names: Sequence[str]) -> str:
    return json.dumps(
        {
            "categories": list(category_names),
            "merchants": [{"id": i, "description": key} for i, key in enumerate(keys)],
        },
        ensure_ascii=False,
    )


def _clean_name(raw: str) -> str | None:
    name = " ".join(raw.split())
    if not name or len(name) > MAX_NAME_LENGTH or contains_placeholder(name):
        return None
    return name


def suggest_merchants(
    client: LLMClient, keys: Sequence[str], categories: Mapping[str, int]
) -> list[MerchantSuggestion]:
    """Suggestions for `keys`, in batches of BATCH_SIZE. Keys the model skipped are omitted.

    Raises LLMError if a batch fails; earlier batches' results are not returned, so callers
    that want partial progress should pass one batch at a time.
    """
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    names = sorted(categories)
    schema = response_schema(names)
    out: list[MerchantSuggestion] = []
    for start in range(0, len(keys), BATCH_SIZE):
        batch = list(keys[start : start + BATCH_SIZE])
        response = client.structured(
            prompt, user_content(batch, names), schema=schema, output_model=_Response
        )
        seen: set[int] = set()
        for item in response.merchants:
            if not 0 <= item.id < len(batch) or item.id in seen:
                continue
            seen.add(item.id)
            category_id = categories.get(item.category)  # UNKNOWN / invalid -> None
            out.append(
                MerchantSuggestion(
                    key=batch[item.id],
                    clean_name=_clean_name(item.clean_name),
                    category_id=category_id,
                    confidence=item.confidence if category_id is not None else None,
                )
            )
    return out
