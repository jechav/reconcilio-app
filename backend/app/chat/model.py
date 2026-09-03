"""The chat agent's final-answer LLM call (issue #11).

Mirrors app/extraction/llm.py and app/extraction/categorize.py: a Protocol
so tests inject a fake, a real LiteLLM-backed implementation, and a Null
fallback for when no LLM is configured. Unlike NullRefiner/NullClassifier
(which must always emit *some* usable field/category), `NullChatModel`
degrades to a templated answer built straight from the retrieved context --
still cites real sources, just without a model's prose synthesizing them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ContextItem:
    """One retrieved piece of evidence handed to the chat model as context.

    `label` is a short human-readable reference the model is asked to weave
    into its answer (e.g. "Transaction abc123..."); `content` is the actual
    text (a Transaction's description/amount/date, or a Document's embedded
    summary).
    """

    label: str
    content: str


class ChatModel(Protocol):
    #: Same purpose as LlmRefiner.PROVIDER -- non-None only for a client
    #: that makes a real, billable call.
    PROVIDER: str | None

    def answer(self, question: str, context: list[ContextItem]) -> str: ...


_SYSTEM_PROMPT = (
    "You answer questions about a small business's financial data using "
    "only the provided context (their own Transactions and Documents). "
    "Never invent a figure that isn't in the context. Refer to each piece "
    "of context you use by its label, e.g. \"(see Transaction abc123)\", so "
    "the answer's sources are traceable. If the context doesn't contain "
    "enough information to answer, say so plainly."
)


class LiteLLMChatModel:
    """Real implementation: one `litellm.completion` call per question."""

    PROVIDER: str | None = "litellm"

    def __init__(self, model: str) -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def answer(self, question: str, context: list[ContextItem]) -> str:
        import litellm

        user_content = {
            "question": question,
            "context": [{"label": item.label, "content": item.content} for item in context],
        }
        response = litellm.completion(
            model=self._model,
            temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_content, default=str)},
            ],
        )
        content = response.choices[0].message.content
        return content or ""


class NullChatModel:
    """Used when no LLM is configured: never calls out, answers with a
    plain listing of the retrieved context instead of model-written prose --
    still grounded and citable, just not synthesized (see module docstring).
    """

    PROVIDER: str | None = None

    def answer(self, question: str, context: list[ContextItem]) -> str:
        if not context:
            return "I couldn't find any Transactions or Documents matching your question."
        lines = [f"- {item.label}: {item.content}" for item in context]
        return "Here is what I found:\n" + "\n".join(lines)
