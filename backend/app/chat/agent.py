"""The RAG chat agent (issue #11).

Graph shape:

    vector_search -> structured_query -> generate_answer

`vector_search` runs the vector-search tool over the calling Organization's
Document/Transaction embeddings to find candidates; `structured_query` takes
those candidates' ids and re-fetches the authoritative Transaction rows
(amount/date/status) through the structured-query tool, still re-scoped by
org_id rather than trusted from the vector hit; `generate_answer` hands both
to the chat model and returns an answer plus the exact sources it cites.
Two tool nodes and a final-answer node, callable as a plain function --
`run_chat(db, org_id, question, deps)` -- the same "pipeline-as-a-function"
shape as app/pipeline.py, so it's independently testable with fakes only at
the embedding-client/chat-model network boundaries (see app/chat/model.py,
app/extraction/embed.py). No tool here can mutate data -- both are read-only
SELECTs (app/chat/tools.py) -- chat is read-only end to end.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.chat.model import ChatModel, ContextItem, NullChatModel
from app.chat.tools import EmbeddingHit, query_transactions, search_embeddings
from app.extraction.embed import EmbeddingClient, NullEmbeddingClient
from app.models import EmbeddingSourceType, Transaction


@dataclass
class ChatDeps:
    """The chat agent's network boundaries, injectable for tests -- same
    role as app/pipeline.py's PipelineDeps."""

    embedding_client: EmbeddingClient = field(default_factory=NullEmbeddingClient)
    chat_model: ChatModel = field(default_factory=NullChatModel)


def default_chat_deps() -> ChatDeps:
    from app.chat.model import LiteLLMChatModel
    from app.config import get_settings
    from app.extraction.embed import LiteLLMEmbeddingClient

    settings = get_settings()
    if not settings.openrouter_api_key:
        return ChatDeps()
    return ChatDeps(
        embedding_client=LiteLLMEmbeddingClient(model=settings.embedding_model),
        chat_model=LiteLLMChatModel(model=settings.chat_model),
    )


def get_chat_deps() -> ChatDeps:
    """FastAPI dependency wrapping `default_chat_deps` -- overridden in tests
    (`app.dependency_overrides[get_chat_deps] = ...`) the same way `get_db`
    is, so an API-boundary test can inject a fake chat model/embedding
    client without ever needing a live OpenRouter key (issue #11)."""
    return default_chat_deps()


@dataclass(frozen=True)
class Citation:
    source_type: EmbeddingSourceType
    source_id: uuid.UUID
    document_id: uuid.UUID
    transaction_id: uuid.UUID | None

    def to_json(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type.value,
            "source_id": str(self.source_id),
            "document_id": str(self.document_id),
            "transaction_id": str(self.transaction_id) if self.transaction_id else None,
        }


@dataclass(frozen=True)
class ChatAnswer:
    answer: str
    citations: list[Citation]


class ChatState(TypedDict, total=False):
    org_id: uuid.UUID
    question: str
    hits: list[EmbeddingHit]
    transactions: list[Transaction]
    answer: str
    citations: list[Citation]


def _build_graph(db: Session, deps: ChatDeps) -> Any:
    def vector_search(state: ChatState) -> ChatState:
        hits = search_embeddings(
            db,
            deps.embedding_client,
            org_id=state["org_id"],
            query_text=state["question"],
        )
        return {**state, "hits": hits}

    def structured_query(state: ChatState) -> ChatState:
        hits = state.get("hits", [])
        transaction_ids = {hit.source_id for hit in hits if hit.source_type == EmbeddingSourceType.transaction}
        document_ids = {hit.document_id for hit in hits if hit.source_type == EmbeddingSourceType.document}

        transactions: list[Transaction] = []
        seen: set[uuid.UUID] = set()
        if transaction_ids:
            for txn in query_transactions(
                db, org_id=state["org_id"], transaction_ids=list(transaction_ids)
            ):
                if txn.id not in seen:
                    transactions.append(txn)
                    seen.add(txn.id)
        if document_ids:
            for txn in query_transactions(
                db, org_id=state["org_id"], document_ids=list(document_ids), limit=1000
            ):
                if txn.id not in seen:
                    transactions.append(txn)
                    seen.add(txn.id)

        return {**state, "transactions": transactions}

    def generate_answer(state: ChatState) -> ChatState:
        hits = state.get("hits", [])
        transactions = state.get("transactions", [])
        transactions_by_id = {t.id: t for t in transactions}

        context: list[ContextItem] = []
        citations: list[Citation] = []
        seen_labels: set[str] = set()

        for hit in hits:
            if hit.source_type == EmbeddingSourceType.transaction:
                txn = transactions_by_id.get(hit.source_id)
                label = f"Transaction {hit.source_id}"
                content = (
                    f"{txn.txn_date} {txn.description} amount={txn.amount} status={txn.status.value}"
                    if txn is not None
                    else hit.content
                )
                citation = Citation(
                    source_type=hit.source_type,
                    source_id=hit.source_id,
                    document_id=hit.document_id,
                    transaction_id=hit.source_id,
                )
            else:
                label = f"Document {hit.document_id}"
                content = hit.content
                citation = Citation(
                    source_type=hit.source_type,
                    source_id=hit.source_id,
                    document_id=hit.document_id,
                    transaction_id=None,
                )

            if label not in seen_labels:
                context.append(ContextItem(label=label, content=content))
                citations.append(citation)
                seen_labels.add(label)

        answer = deps.chat_model.answer(state["question"], context)
        return {**state, "answer": answer, "citations": citations}

    graph = StateGraph(ChatState)
    graph.add_node("vector_search", vector_search)
    graph.add_node("structured_query", structured_query)
    graph.add_node("generate_answer", generate_answer)
    graph.add_edge(START, "vector_search")
    graph.add_edge("vector_search", "structured_query")
    graph.add_edge("structured_query", "generate_answer")
    graph.add_edge("generate_answer", END)
    return graph.compile()


def run_chat(
    db: Session,
    *,
    org_id: uuid.UUID,
    question: str,
    deps: ChatDeps | None = None,
) -> ChatAnswer:
    """Answer one chat question, scoped end-to-end to `org_id`.

    Callable directly (no HTTP) so it's a pipeline-as-a-function test target,
    same as app/pipeline.py's run_pipeline. `deps` defaults to the real
    LiteLLM-backed clients (Null when no OpenRouter key is configured);
    tests inject fakes so no live credentials are ever required.
    """
    if deps is None:
        deps = default_chat_deps()

    result = _build_graph(db, deps).invoke({"org_id": org_id, "question": question})
    return ChatAnswer(answer=result.get("answer", ""), citations=result.get("citations", []))
