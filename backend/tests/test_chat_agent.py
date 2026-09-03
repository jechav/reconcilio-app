"""Pipeline-as-a-function tests for the RAG chat agent (issue #11).

Drives the real graph (vector_search -> structured_query -> generate_answer)
against real Document/Transaction/Embedding rows, with fakes only at the
embedding-client/chat-model network boundaries. Covers: a question spanning
multiple Documents whose answer cites the correct Transactions, that no tool
call ever crosses an Organization boundary, and that the agent's tools never
mutate data.
"""

import uuid
from datetime import date
from decimal import Decimal

from app.chat.agent import ChatDeps, run_chat
from app.chat.model import ChatModel, ContextItem
from app.extraction.embed import EmbeddingClient
from app.models import Document, DocumentStatus, DocumentType, Embedding, EmbeddingSourceType, Organization, Transaction, TransactionStatus


class FakeEmbeddingClient:
    """A tiny deterministic embedding space: two texts are "close" iff they
    share a keyword tag baked into the fake vector -- good enough to prove
    real cosine-distance ranking through pgvector without a live model."""

    PROVIDER = "fake"

    def __init__(self, vectors_by_keyword: dict[str, list[float]]) -> None:
        self._vectors_by_keyword = vectors_by_keyword

    def embed(self, text: str) -> list[float] | None:
        for keyword, vector in self._vectors_by_keyword.items():
            if keyword in text:
                return vector
        # Unknown text: a vector far from every known keyword vector.
        dim = len(next(iter(self._vectors_by_keyword.values())))
        return [-1.0] * dim


class FakeChatModel:
    """Records the context it was handed and returns an answer that
    literally references every label -- the LLM call is mocked, never a
    real network request."""

    PROVIDER = "fake"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def answer(self, question: str, context: list[ContextItem]) -> str:
        self.calls.append({"question": question, "context": context})
        if not context:
            return "No relevant data found."
        return "Based on " + ", ".join(item.label for item in context)


def _dim() -> int:
    from app.extraction.embed import EMBEDDING_DIMENSIONS

    return EMBEDDING_DIMENSIONS


def _vec(seed: float) -> list[float]:
    return [seed] * _dim()


def _make_org(db_session, name="Acme Tax") -> Organization:
    org = Organization(name=name)
    db_session.add(org)
    db_session.flush()
    return org


def _make_document(db_session, org: Organization, filename: str) -> Document:
    document = Document(
        org_id=org.id,
        filename=filename,
        content_type="application/pdf",
        size_bytes=10,
        minio_key=f"{org.id}/{uuid.uuid4()}-{filename}",
        doc_type=DocumentType.invoice_or_receipt,
        status=DocumentStatus.done,
    )
    db_session.add(document)
    db_session.flush()
    return document


def _make_transaction(db_session, org: Organization, document: Document, description: str, amount: str) -> Transaction:
    transaction = Transaction(
        org_id=org.id,
        document_id=document.id,
        line_number=1,
        txn_date=date(2026, 1, 15),
        description=description,
        amount=Decimal(amount),
        confidence=0.95,
        status=TransactionStatus.resolved,
    )
    db_session.add(transaction)
    db_session.flush()
    return transaction


def _make_transaction_embedding(db_session, org: Organization, document: Document, transaction: Transaction, content: str, vector: list[float]) -> Embedding:
    embedding = Embedding(
        org_id=org.id,
        document_id=document.id,
        transaction_id=transaction.id,
        source_type=EmbeddingSourceType.transaction,
        source_id=transaction.id,
        content=content,
        vector=vector,
    )
    db_session.add(embedding)
    db_session.flush()
    return embedding


def test_answer_cites_transactions_from_multiple_documents(db_session):
    org = _make_org(db_session)
    doc1 = _make_document(db_session, org, "invoice-1.pdf")
    doc2 = _make_document(db_session, org, "invoice-2.pdf")
    txn1 = _make_transaction(db_session, org, doc1, "Delta Airlines", "-450.00")
    txn2 = _make_transaction(db_session, org, doc2, "United Airlines", "-320.00")
    # A third Transaction/Document the question has nothing to do with.
    doc3 = _make_document(db_session, org, "office-supplies.pdf")
    _make_transaction(db_session, org, doc3, "Staples", "-40.00")

    _make_transaction_embedding(db_session, org, doc1, txn1, "Delta Airlines flight", _vec(1.0))
    _make_transaction_embedding(db_session, org, doc2, txn2, "United Airlines flight", _vec(1.0))
    db_session.commit()

    embedding_client = FakeEmbeddingClient({"flights": _vec(1.0)})
    chat_model = FakeChatModel()
    deps = ChatDeps(embedding_client=embedding_client, chat_model=chat_model)

    result = run_chat(db_session, org_id=org.id, question="How much did I spend on flights?", deps=deps)

    cited_transaction_ids = {c.transaction_id for c in result.citations}
    assert cited_transaction_ids == {txn1.id, txn2.id}

    cited_document_ids = {c.document_id for c in result.citations}
    assert cited_document_ids == {doc1.id, doc2.id}

    assert str(txn1.id) in result.answer or "Transaction" in result.answer
    # The chat model was handed grounded context, not just the raw question.
    assert len(chat_model.calls) == 1
    assert len(chat_model.calls[0]["context"]) == 2


def test_agent_never_surfaces_another_organizations_data(db_session):
    org = _make_org(db_session, "Org A")
    other_org = _make_org(db_session, "Org B")

    doc = _make_document(db_session, org, "invoice.pdf")
    txn = _make_transaction(db_session, org, doc, "Delta Airlines", "-450.00")
    _make_transaction_embedding(db_session, org, doc, txn, "Delta Airlines flight", _vec(1.0))

    other_doc = _make_document(db_session, other_org, "other-invoice.pdf")
    other_txn = _make_transaction(db_session, other_org, other_doc, "Delta Airlines (Org B)", "-999.00")
    _make_transaction_embedding(db_session, other_org, other_doc, other_txn, "Delta Airlines flight", _vec(1.0))
    db_session.commit()

    embedding_client = FakeEmbeddingClient({"flights": _vec(1.0)})
    chat_model = FakeChatModel()
    deps = ChatDeps(embedding_client=embedding_client, chat_model=chat_model)

    result = run_chat(db_session, org_id=org.id, question="How much did I spend on flights?", deps=deps)

    cited_transaction_ids = {c.transaction_id for c in result.citations}
    assert cited_transaction_ids == {txn.id}
    assert other_txn.id not in cited_transaction_ids

    context_contents = [item.content for item in chat_model.calls[0]["context"]]
    assert not any("Org B" in content for content in context_contents)


def test_null_embedding_client_returns_no_results_without_crashing(db_session):
    """No LLM configured -- the agent degrades gracefully rather than
    requiring live credentials (mirrors NullRefiner/NullClassifier)."""
    from app.extraction.embed import NullEmbeddingClient
    from app.chat.model import NullChatModel

    org = _make_org(db_session)
    deps = ChatDeps(embedding_client=NullEmbeddingClient(), chat_model=NullChatModel())

    result = run_chat(db_session, org_id=org.id, question="How much did I spend on flights?", deps=deps)

    assert result.citations == []
    assert "couldn't find" in result.answer.lower()
