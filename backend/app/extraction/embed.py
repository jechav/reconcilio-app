"""Embedding generation for RAG chat (issue #11).

Mirrors the shape of app/extraction/llm.py and app/extraction/categorize.py:
`LiteLLMEmbeddingClient` is the real implementation, a single
`litellm.embedding` call; `NullEmbeddingClient` is the fallback when no LLM
is configured. Unlike the refiner/classifier Null variants (which must
always produce *something* so a Transaction is never left uncategorized),
`NullEmbeddingClient` returns `None` -- an Embedding row is still persisted
(so the pipeline-as-a-function shape stays uniform across paths) but with no
vector, and the chat agent's vector-search tool simply excludes rows with no
vector from its similarity search (see app/chat/tools.py). This keeps
pipeline and API tests free of any live OpenRouter credential requirement.
"""

from __future__ import annotations

from typing import Protocol

#: text-embedding-3-small's output width -- fixed so every row in the
#: `embeddings` table (see migrations/versions/0008_rag_chat.py) shares one
#: vector dimensionality regardless of which Document/Transaction produced
#: it.
EMBEDDING_DIMENSIONS = 1536

DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"


class EmbeddingClient(Protocol):
    #: Same purpose as LlmRefiner.PROVIDER (app/extraction/llm.py) -- non-None
    #: only on a client that makes a real, billable call.
    PROVIDER: str | None

    def embed(self, text: str) -> list[float] | None:
        """Return an embedding vector for `text`, or None if unavailable."""


class LiteLLMEmbeddingClient:
    """Real client: one `litellm.embedding` call per piece of text."""

    PROVIDER: str | None = "litellm"

    def __init__(self, model: str = DEFAULT_EMBEDDING_MODEL) -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def embed(self, text: str) -> list[float] | None:
        import litellm

        if not text.strip():
            return None
        response = litellm.embedding(model=self._model, input=[text])
        vector = response.data[0]["embedding"]
        return list(vector)


class NullEmbeddingClient:
    """Used when no LLM is configured: never calls out, always returns None.

    See module docstring -- the persist step still records the Embedding
    row (content only) so a later `openrouter_api_key` configuration change
    doesn't require a backfill migration to notice which rows exist.
    """

    PROVIDER: str | None = None

    def embed(self, text: str) -> list[float] | None:
        return None
