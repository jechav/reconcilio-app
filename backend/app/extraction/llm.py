"""Vision-capable LLM refinement pass, via LiteLLM/OpenRouter (issue #3).

Any field Textract returned below the Organization's confidence threshold
gets a second look here: the document image is sent to a vision-capable
model along with the low-confidence field name and Textract's current
guess, and the model returns a corrected value plus its own confidence.

`LLMRefinementClient` is a Protocol so tests inject a fake; the real
`LiteLLMRefinementClient` is a thin wrapper around `litellm.completion`
(never called in tests -- no live OpenRouter credentials in this sandbox).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

DEFAULT_MODEL = "openrouter/anthropic/claude-3-haiku"

_PROMPT_TEMPLATE = (
    "You are extracting the field '{field_name}' from the attached invoice or "
    "receipt image. Textract's first-pass guess for this field was: {current_value!r} "
    "(low confidence). Look at the image and return your best value for this field. "
    "Respond with ONLY a JSON object of the form "
    '{{"value": "<the field value as plain text, or null if not present>", '
    '"confidence": <your confidence from 0.0 to 1.0>}}.'
)


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
                            "text": _PROMPT_TEMPLATE.format(
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
