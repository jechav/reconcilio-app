import uuid

from app.models import Document, DocumentStatus, DocumentType, Organization
from app.pipeline import run_pipeline


def _make_document(db_session) -> Document:
    org = Organization(name="Acme Tax")
    db_session.add(org)
    db_session.flush()

    document = Document(
        org_id=org.id,
        filename="invoice.pdf",
        content_type="application/pdf",
        size_bytes=1234,
        minio_key=f"{org.id}/{uuid.uuid4()}-invoice.pdf",
        doc_type=DocumentType.invoice_or_receipt,
        status=DocumentStatus.queued,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document


def test_pipeline_drives_document_from_queued_to_done(db_session):
    document = _make_document(db_session)
    assert document.status == DocumentStatus.queued

    result = run_pipeline(document.id, db_session)

    assert result.status == DocumentStatus.done

    persisted = db_session.get(Document, document.id)
    assert persisted is not None
    assert persisted.status == DocumentStatus.done


def test_pipeline_raises_for_unknown_document(db_session):
    try:
        run_pipeline(uuid.uuid4(), db_session)
        assert False, "expected ValueError"
    except ValueError:
        pass
