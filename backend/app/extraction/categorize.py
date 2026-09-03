"""LLM-based Category suggestion for Transactions (issue #5).

Given a Transaction's description/amount, the Organization's flat Category
list, and its most recent user corrections (used as few-shot examples so
future suggestions learn from past corrections on *that* Organization only
-- CONTEXT.md, Category), suggest exactly one Category name with a
confidence score.

Mirrors the shape of app/extraction/llm.py: `OpenRouterCategoryClassifier`
is the real implementation, a single OpenAI-compatible chat completion via
OpenRouter; `NullClassifier` is the fallback when no LLM is configured. Both
always return a suggestion so every Transaction gets exactly one suggested
Category (issue #5, AC3) -- `NullClassifier` degrades to "Other" at zero
confidence rather than leaving the Transaction uncategorized, the same
"never silently promote, always flag for a human" posture as
llm.NullRefiner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

#: Suggested when nothing more specific applies, or when no Category list
#: is available to choose from at all.
DEFAULT_CATEGORY = "Other"

#: How many of the Organization's most recent corrections to show the model
#: as few-shot examples. Bounded so prompt size doesn't grow unbounded with
#: an Organization's history.
FEW_SHOT_EXAMPLE_LIMIT = 10


@dataclass(frozen=True)
class CategorySuggestion:
    category_name: str
    confidence: float  # 0.0-1.0


@dataclass(frozen=True)
class CorrectionExample:
    """One past user correction, as few-shot context for a new suggestion."""

    description: str
    category_name: str


class CategoryClassifier(Protocol):
    def suggest(
        self,
        description: str,
        amount: str,
        category_names: list[str],
        examples: list[CorrectionExample],
    ) -> CategorySuggestion: ...


_SYSTEM_PROMPT = (
    "You categorize financial transactions for tax preparation. Choose "
    "exactly one category from the provided list that best matches the "
    "transaction; past corrections (if any) show how this organization "
    "prefers similar transactions to be categorized. Reply with JSON only: "
    '{"category": "<name>", "confidence": <0-1 number>}. The category must '
    "be exactly one of the provided names, verbatim."
)


class OpenRouterCategoryClassifier:
    """Real classifier: one OpenAI-compatible chat completion via OpenRouter."""

    def __init__(self, api_key: str, model: str, base_url: str, timeout_seconds: float = 60.0) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def suggest(
        self,
        description: str,
        amount: str,
        category_names: list[str],
        examples: list[CorrectionExample],
    ) -> CategorySuggestion:
        if not category_names:
            return CategorySuggestion(category_name=DEFAULT_CATEGORY, confidence=0.0)

        import httpx

        user_content = {
            "transaction": {"description": description, "amount": amount},
            "categories": category_names,
            "past_corrections": [
                {"description": e.description, "category": e.category_name}
                for e in examples[:FEW_SHOT_EXAMPLE_LIMIT]
            ],
        }
        payload = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_content, default=str)},
            ],
        }
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=payload,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return parse_suggestion(content, category_names)


class NullClassifier:
    """Used when no LLM is configured: always suggests "Other" (or the first
    available Category if "Other" was deleted/renamed) at zero confidence,
    so a Transaction still gets exactly one Category and the review UI is
    where a human corrects it -- see llm.NullRefiner for the same pattern.
    """

    def suggest(
        self,
        description: str,
        amount: str,
        category_names: list[str],
        examples: list[CorrectionExample],
    ) -> CategorySuggestion:
        if not category_names:
            return CategorySuggestion(category_name=DEFAULT_CATEGORY, confidence=0.0)
        fallback = DEFAULT_CATEGORY if DEFAULT_CATEGORY in category_names else category_names[0]
        return CategorySuggestion(category_name=fallback, confidence=0.0)


def parse_suggestion(content: str, category_names: list[str]) -> CategorySuggestion:
    """Read the model's JSON reply, falling back to the first known category
    (at zero confidence) for anything unparseable or hallucinated."""
    fallback = category_names[0] if category_names else DEFAULT_CATEGORY

    try:
        parsed: Any = json.loads(content)
    except json.JSONDecodeError:
        return CategorySuggestion(category_name=fallback, confidence=0.0)
    if not isinstance(parsed, dict):
        return CategorySuggestion(category_name=fallback, confidence=0.0)

    name = parsed.get("category")
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    if not isinstance(name, str) or name not in category_names:
        # The model named a category that isn't in the list it was given --
        # never persist a nonexistent Category, fall back instead.
        return CategorySuggestion(category_name=fallback, confidence=0.0)
    return CategorySuggestion(category_name=name, confidence=confidence)
