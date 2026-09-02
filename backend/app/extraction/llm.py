"""LLM refinement pass for fields Textract wasn't confident about.

Only the low-confidence fields of a line are sent for a second opinion, so
the cost scales with how bad the scan is rather than with statement length.
Refined fields come back marked `llm` with the model's own reported
confidence, which keeps the per-field provenance honest: a value corrected
by the model is never presented as if OCR had read it cleanly.

The refiner is a Protocol; the OpenRouter implementation is real but is
never exercised in tests (no API key in the sandbox) -- tests substitute a
fake at this boundary.
"""

import json
from typing import Any, Protocol

from app.extraction.types import ExtractedField, ExtractedLine
from app.models import ExtractionMethod

_SYSTEM_PROMPT = (
    "You correct OCR errors on bank statement lines. You are given the fields "
    "of one line and the names of the fields the OCR was unsure about. Reply "
    "with JSON only: {\"fields\": {\"<name>\": {\"value\": <string>, "
    "\"confidence\": <0-1 number>}}} containing only the uncertain fields. "
    "Dates must be YYYY-MM-DD; amounts must be plain signed decimals "
    "(negative = money out). If you cannot improve a field, return it "
    "unchanged with a low confidence."
)


class LlmRefiner(Protocol):
    """Second-pass correction of specific low-confidence fields."""

    def refine(self, line: ExtractedLine, field_names: list[str]) -> dict[str, ExtractedField]:
        """Return replacements for (a subset of) `field_names`."""


class OpenRouterRefiner:
    """Real refiner: an OpenAI-compatible chat completion via OpenRouter."""

    def __init__(self, api_key: str, model: str, base_url: str, timeout_seconds: float = 60.0) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def refine(self, line: ExtractedLine, field_names: list[str]) -> dict[str, ExtractedField]:
        import httpx

        payload = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"line": line.to_json(), "uncertain_fields": field_names}, default=str
                    ),
                },
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
        return parse_refinement(content, field_names)


class NullRefiner:
    """Used when no LLM is configured: leaves every field exactly as it was.

    Low-confidence fields then stay low-confidence and their Transactions are
    flagged for review, which is the correct degradation -- silently
    promoting unverified OCR output would be worse than asking a human.
    """

    def refine(self, line: ExtractedLine, field_names: list[str]) -> dict[str, ExtractedField]:
        return {}


def parse_refinement(content: str, field_names: list[str]) -> dict[str, ExtractedField]:
    """Read the model's JSON reply, ignoring anything it wasn't asked about."""
    try:
        parsed: Any = json.loads(content)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    fields = parsed.get("fields", parsed)
    if not isinstance(fields, dict):
        return {}

    refined: dict[str, ExtractedField] = {}
    for name in field_names:
        entry = fields.get(name)
        if not isinstance(entry, dict) or "value" not in entry:
            continue
        value = entry["value"]
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        refined[name] = ExtractedField(
            value=None if value is None else str(value),
            confidence=max(0.0, min(1.0, confidence)),
            method=ExtractionMethod.llm,
        )
    return refined
