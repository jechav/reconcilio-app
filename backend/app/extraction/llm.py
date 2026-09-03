"""LLM refinement: the second pass for fields Textract wasn't confident about.

Two refiner shapes coexist here because the two extraction paths hand the
model genuinely different context:

- `LlmRefiner` (text-based, bank statement path, issue #4): only the
  low-confidence fields of one statement *line* are sent for a second
  opinion, so cost scales with how bad the scan is rather than with
  statement length. `OpenRouterRefiner` is the real implementation, a
  single OpenAI-compatible chat completion per line via OpenRouter.
- `LLMRefinementClient` (vision-based, invoice/receipt path, issue #3): one
  low-confidence *field* at a time is re-read from the document image
  itself via a vision-capable model, since an invoice/receipt has no
  cheaper structured representation to fall back on the way a statement
  line's other fields do. `LiteLLMRefinementClient` is the real
  implementation, via `litellm.completion`.

Both are Protocols so tests inject fakes; neither real implementation is
ever exercised in tests (no live OpenRouter credentials in this sandbox).
Refined fields always come back marked `llm` with the model's own reported
confidence, which keeps the per-field provenance honest: a value corrected
by the model is never presented as if OCR had read it cleanly.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from app.extraction.types import ExtractedField, ExtractedLine
from app.models import ExtractionMethod

DEFAULT_MODEL = "openrouter/anthropic/claude-3-haiku"

_LINE_SYSTEM_PROMPT = (
    "You correct OCR errors on bank statement lines. You are given the fields "
    "of one line and the names of the fields the OCR was unsure about. Reply "
    "with JSON only: {\"fields\": {\"<name>\": {\"value\": <string>, "
    "\"confidence\": <0-1 number>}}} containing only the uncertain fields. "
    "Dates must be YYYY-MM-DD; amounts must be plain signed decimals "
    "(negative = money out). If you cannot improve a field, return it "
    "unchanged with a low confidence."
)

_FIELD_PROMPT_TEMPLATE = (
    "You are extracting the field '{field_name}' from the attached invoice or "
    "receipt image. Textract's first-pass guess for this field was: {current_value!r} "
    "(low confidence). Look at the image and return your best value for this field. "
    "Respond with ONLY a JSON object of the form "
    '{{"value": "<the field value as plain text, or null if not present>", '
    '"confidence": <your confidence from 0.0 to 1.0>}}.'
)


# --- text-based: bank statement lines (issue #4) ---------------------------


class LlmRefiner(Protocol):
    """Second-pass correction of specific low-confidence fields on one line."""

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
                {"role": "system", "content": _LINE_SYSTEM_PROMPT},
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


# --- vision-based: invoice/receipt fields (issue #3) ------------------------


@dataclass(frozen=True)
class RefinedField:
    value: str | None
    confidence: float  # 0.0-1.0


class LLMRefinementClient(Protocol):
    def refine_field(
        self,
        field_name: str,
        document_bytes: bytes,
        content_type: str,
        current_value: str | None,
    ) -> RefinedField: ...


class LiteLLMRefinementClient:
    """Real implementation: one vision-model call per low-confidence field."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model = model

    def refine_field(
        self,
        field_name: str,
        document_bytes: bytes,
        content_type: str,
        current_value: str | None,
    ) -> RefinedField:
        import litellm

        encoded = base64.b64encode(document_bytes).decode("ascii")
        data_url = f"data:{content_type};base64,{encoded}"
        response = litellm.completion(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _FIELD_PROMPT_TEMPLATE.format(
                                field_name=field_name, current_value=current_value
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
        content = response.choices[0].message.content
        parsed = json.loads(content)
        return RefinedField(value=parsed.get("value"), confidence=float(parsed.get("confidence", 0.0)))


@lru_cache
def get_llm_client() -> LLMRefinementClient:
    return LiteLLMRefinementClient()
