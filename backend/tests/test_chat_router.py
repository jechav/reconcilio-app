"""API-boundary tests for the chat endpoints (issue #11).

Covers: ChatSession/ChatMessage history round-trips through the API, a chat
query spanning multiple Documents whose answer cites the correct sources
with the LLM call mocked (AC6/AC7), and that a chat session in one
Organization can never surface another Organization's data (AC3).
"""

import uuid
from datetime import date
from decimal import Decimal

from app.chat.agent import ChatDeps, get_chat_deps
from app.chat.model import ContextItem
from app.main import app
from app.models import Document, DocumentStatus, DocumentType, Embedding, EmbeddingSourceType, Transaction, TransactionStatus


def _signup(client, email, org_name="Acme Tax"):
    response = client.post(
        "/auth/signup",
        json={"email": email, "password": "correct-horse", "org_name": org_name},
    )
    assert response.status_code == 201
    return response.json()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _make_document(db_session, org_id, filename):
    document = Document(
        org_id=org_id,
        filename=filename,
        content_type="application/pdf",
        size_bytes=10,
        minio_key=f"{org_id}/{uuid.uuid4()}-{filename}",
        doc_type=DocumentType.invoice_or_receipt,
        status=DocumentStatus.done,
    )
    db_session.add(document)
    db_session.flush()
    return document


def _make_transaction(db_session, org_id, document, description, amount):
    transaction = Transaction(
        org_id=org_id,
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


def _make_embedding(db_session, org_id, document, transaction, content, vector):
    embedding = Embedding(
        org_id=org_id,
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


class FakeEmbeddingClient:
    PROVIDER = "fake"

    def __init__(self, vector):
        self._vector = vector

    def embed(self, text):
        return self._vector


class FakeChatModel:
    """The LLM call, mocked -- records the context and cites every label."""

    PROVIDER = "fake"

    def answer(self, question: str, context: list[ContextItem]) -> str:
        if not context:
            return "No relevant data found."
        return "Found: " + "; ".join(f"{item.label} ({item.content})" for item in context)


def _dim():
    from app.extraction.embed import EMBEDDING_DIMENSIONS

    return EMBEDDING_DIMENSIONS


def _vec(seed: float):
    return [seed] * _dim()


def _override_chat_deps(vector):
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        embedding_client=FakeEmbeddingClient(vector), chat_model=FakeChatModel()
    )


def test_chat_session_and_message_history_round_trip(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])

    create_resp = client.post("/chat/sessions", headers=headers)
    assert create_resp.status_code == 201
    session = create_resp.json()

    list_resp = client.get("/chat/sessions", headers=headers)
    assert list_resp.status_code == 200
    assert any(s["id"] == session["id"] for s in list_resp.json())

    messages_resp = client.get(f"/chat/sessions/{session['id']}/messages", headers=headers)
    assert messages_resp.status_code == 200
    assert messages_resp.json() == []


def test_chat_query_spanning_multiple_documents_cites_correct_sources(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    doc1 = _make_document(db_session, org_id, "invoice-1.pdf")
    doc2 = _make_document(db_session, org_id, "invoice-2.pdf")
    txn1 = _make_transaction(db_session, org_id, doc1, "Delta Airlines", "-450.00")
    txn2 = _make_transaction(db_session, org_id, doc2, "United Airlines", "-320.00")
    vector = _vec(1.0)
    _make_embedding(db_session, org_id, doc1, txn1, "Delta Airlines flight", vector)
    _make_embedding(db_session, org_id, doc2, txn2, "United Airlines flight", vector)
    db_session.commit()

    _override_chat_deps(vector)
    try:
        session = client.post("/chat/sessions", headers=headers).json()
        response = client.post(
            f"/chat/sessions/{session['id']}/messages",
            headers=headers,
            json={"content": "How much did I spend on flights?"},
        )
    finally:
        app.dependency_overrides.pop(get_chat_deps, None)

    assert response.status_code == 200
    messages = response.json()
    assert len(messages) == 2
    user_message, assistant_message = messages
    assert user_message["role"] == "user"
    assert user_message["content"] == "How much did I spend on flights?"
    assert user_message["citations"] == []

    assert assistant_message["role"] == "assistant"
    cited_transaction_ids = {c["transaction_id"] for c in assistant_message["citations"]}
    assert cited_transaction_ids == {str(txn1.id), str(txn2.id)}
    cited_document_ids = {c["document_id"] for c in assistant_message["citations"]}
    assert cited_document_ids == {str(doc1.id), str(doc2.id)}
    assert "Delta" in assistant_message["content"] or "United" in assistant_message["content"]

    # Persisted history round-trips through GET too.
    history = client.get(f"/chat/sessions/{session['id']}/messages", headers=headers).json()
    assert len(history) == 2


def test_chat_session_in_one_org_cannot_surface_another_orgs_data(client, db_session, unique_email):
    owner_a = _signup(client, unique_email, org_name="Org A")
    org_a_id = uuid.UUID(owner_a["organization"]["id"])
    headers_a = _auth_headers(owner_a["access_token"])

    owner_b = _signup(client, f"other-{unique_email}", org_name="Org B")
    org_b_id = uuid.UUID(owner_b["organization"]["id"])

    doc_b = _make_document(db_session, org_b_id, "org-b-invoice.pdf")
    txn_b = _make_transaction(db_session, org_b_id, doc_b, "Org B's secret vendor", "-9999.00")
    vector = _vec(1.0)
    _make_embedding(db_session, org_b_id, doc_b, txn_b, "Org B's secret vendor flight", vector)
    db_session.commit()

    _override_chat_deps(vector)
    try:
        session = client.post("/chat/sessions", headers=headers_a).json()
        response = client.post(
            f"/chat/sessions/{session['id']}/messages",
            headers=headers_a,
            json={"content": "How much did I spend on flights?"},
        )
    finally:
        app.dependency_overrides.pop(get_chat_deps, None)

    assert response.status_code == 200
    messages = response.json()
    assistant_message = messages[1]
    assert assistant_message["citations"] == []
    assert "Org B" not in assistant_message["content"]
    assert str(txn_b.id) not in str(assistant_message["citations"])


def test_chat_session_not_found_in_other_org_is_404(client, db_session, unique_email):
    owner_a = _signup(client, unique_email, org_name="Org A")
    headers_a = _auth_headers(owner_a["access_token"])
    owner_b = _signup(client, f"other-{unique_email}", org_name="Org B")
    headers_b = _auth_headers(owner_b["access_token"])

    session = client.post("/chat/sessions", headers=headers_a).json()

    response = client.get(f"/chat/sessions/{session['id']}/messages", headers=headers_b)
    assert response.status_code == 404
