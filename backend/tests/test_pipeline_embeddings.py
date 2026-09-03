"""Pipeline-as-a-function tests for embedding generation on persist (issue
#11, AC1).

Drives the real graph end to end with the embedding client -- the only new
network boundary this ticket adds to the pipeline -- faked, alongside the
existing Textract fake. No LLM credentials are needed or used.
"""

import uuid
from decimal import Decimal

from app.extraction.embed import EmbeddingClient, NullEmbeddingClient
from app.extraction.llm import NullRefiner
from app.extraction.textract import ExtractedField, TextractExpenseResult
from app.models import (
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    EmbeddingSourceType,
    Organization,
    Transaction,
)
from app.pipeline import PipelineDeps, run_pipeline

FAKE_PDF_BYTES = b"%PDF-1.4 fake invoice bytes for testing"


class FakeTextractClient:
    def __init__(self, expense_fields: list[ExtractedField]) -> None:
        self._expense_fields = expense_fields

    def detect_text(self, document_bytes):
        return ["INVOICE"]

    def analyze_expense(self, document_bytes):
        return TextractExpenseResult(fields=self._expense_fields)

    def analyze_document(self, document_bytes):  # pragma: no cover
        raise AssertionError("invoice/receipt path never calls analyze_document")


class FakeEmbeddingClient:
    """Returns a deterministic, distinguishable vector per piece of text --
    the LLM call is mocked, never a real network request."""

    PROVIDER = "fake"

    def __init__(self) -> None:
        self.embedded_texts: list[str] = []

    def embed(self, text: str) -> list[float] | None:
        self.embedded_texts.append(text)
        # A short deterministic vector padded to the real dimensionality
        # would work too, but the model column is fixed-width -- pad with a
        # stable value derived from the text so two different texts produce
        # two different (but reproducible) vectors.
        from app.extraction.embed import EMBEDDING_DIMENSIONS

        seed = float(len(text) % 7) / 10.0
        return [seed] * EMBEDDING_DIMENSIONS


def _make_org(db_session) -> Organization:
    org = Organization(name="Acme Tax")
    db_session.add(org)
    db_session.flush()
    return org


def _make_document(db_session, org: Organization) -> Document:
    document = Document(
        org_id=org.id,
        filename="invoice.pdf",
        content_type="application/pdf",
        size_bytes=len(FAKE_PDF_BYTES),
        minio_key=f"{org.id}/{uuid.uuid4()}-invoice.pdf",
        doc_type=DocumentType.invoice_or_receipt,
        status=DocumentStatus.queued,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document


def _deps(textract, embedding_client: EmbeddingClient) -> PipelineDeps:
    return PipelineDeps(
        fetch_bytes=lambda key: FAKE_PDF_BYTES,
        textract=textract,
        refiner=NullRefiner(),
        llm_client=None,
        embedding_client=embedding_client,
    )


def _expense_fields() -> list[ExtractedField]:
    return [
        ExtractedField(name="vendor", value="Airline Co", confidence=0.95),
        ExtractedField(name="amount", value="250.00", confidence=0.95),
        ExtractedField(name="invoice_date", value="2026-01-01", confidence=0.95),
    ]


def test_persist_writes_a_transaction_and_document_embedding(db_session):
    org = _make_org(db_session)
    document = _make_document(db_session, org)
    embedding_client = FakeEmbeddingClient()

    run_pipeline(document.id, db_session, _deps(FakeTextractClient(_expense_fields()), embedding_client))

    transaction = db_session.query(Transaction).filter_by(document_id=document.id).one()

    embeddings = db_session.query(Embedding).filter_by(org_id=org.id).all()
    by_source_type = {e.source_type: e for e in embeddings}
    assert set(by_source_type) == {EmbeddingSourceType.transaction, EmbeddingSourceType.document}

    txn_embedding = by_source_type[EmbeddingSourceType.transaction]
    assert txn_embedding.source_id == transaction.id
    assert txn_embedding.transaction_id == transaction.id
    assert txn_embedding.document_id == document.id
    assert "Airline Co" in txn_embedding.content
    assert txn_embedding.vector is not None
    assert len(txn_embedding.vector) == 1536

    doc_embedding = by_source_type[EmbeddingSourceType.document]
    assert doc_embedding.source_id == document.id
    assert doc_embedding.document_id == document.id
    assert doc_embedding.transaction_id is None
    assert "invoice.pdf" in doc_embedding.content
    assert "Airline Co" in doc_embedding.content
    assert doc_embedding.vector is not None

    # Both the Transaction content and the Document summary were sent to
    # the embedding client -- a real, billable call each, not skipped.
    assert len(embedding_client.embedded_texts) == 2


def test_null_embedding_client_still_writes_rows_with_no_vector(db_session):
    """No LLM configured (NullEmbeddingClient, the pipeline-as-a-function
    default) -- pipeline never requires live credentials, and the pipeline
    shape stays uniform: rows are created, just with `vector` unset."""
    org = _make_org(db_session)
    document = _make_document(db_session, org)

    run_pipeline(document.id, db_session, _deps(FakeTextractClient(_expense_fields()), NullEmbeddingClient()))

    embeddings = db_session.query(Embedding).filter_by(org_id=org.id).all()
    assert len(embeddings) == 2
    assert all(e.vector is None for e in embeddings)


def test_rerunning_persist_upserts_rather_than_duplicating_embeddings(db_session):
    """Idempotency (issue #7, AC3/AC6 posture): a second persist for the
    same Document (e.g. the existing-transactions defense-in-depth path in
    `_persist_lines`) refreshes the embedding rows in place instead of
    violating the (source_type, source_id) uniqueness constraint."""
    from app.pipeline import PipelineDeps, _build_graph

    org = _make_org(db_session)
    document = _make_document(db_session, org)
    document.status = DocumentStatus.processing
    db_session.commit()

    deps = _deps(FakeTextractClient(_expense_fields()), FakeEmbeddingClient())
    categories: list = []
    examples: list = []

    graph = _build_graph(document, db_session, deps, 0.8, categories, examples)
    graph.invoke({"document_id": str(document.id)})
    db_session.commit()

    first_pass_count = db_session.query(Embedding).filter_by(org_id=org.id).count()
    assert first_pass_count == 2

    # Re-invoke persist directly (bypassing run_pipeline's terminal-status
    # short-circuit, which is exercised elsewhere) to hit the upsert path.
    graph2 = _build_graph(document, db_session, deps, 0.8, categories, examples)
    graph2.invoke({"document_id": str(document.id)})
    db_session.commit()

    second_pass_count = db_session.query(Embedding).filter_by(org_id=org.id).count()
    assert second_pass_count == 2
