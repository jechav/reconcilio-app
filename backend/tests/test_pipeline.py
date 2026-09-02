import uuid

from app.extraction.llm import RefinedField
from app.extraction.textract import ExtractedField, TextractExpenseResult
from app.models import (
    AuditLogEntry,
    Document,
    DocumentStatus,
    DocumentType,
    ExtractionMethod,
    ExtractionResult,
    Organization,
    ReviewStatus,
    Transaction,
)
from app.pipeline import run_pipeline

FAKE_PDF_BYTES = b"%PDF-1.4 fake invoice bytes for testing"


class FakeTextractClient:
    """Records the calls it received so tests can assert what the pipeline
    asked of Textract, without touching AWS."""

    def __init__(self, lines: list[str], expense_fields: list[ExtractedField]) -> None:
        self._lines = lines
        self._expense_fields = expense_fields
        self.analyze_expense_calls = 0

    def detect_text(self, document_bytes: bytes) -> list[str]:
        return self._lines

    def analyze_expense(self, document_bytes: bytes) -> TextractExpenseResult:
        self.analyze_expense_calls += 1
        return TextractExpenseResult(fields=self._expense_fields)


class FakeLLMClient:
    """Returns a canned refinement and records which fields were asked for."""

    def __init__(self, refinements: dict[str, RefinedField]) -> None:
        self._refinements = refinements
        self.refined_fields: list[str] = []

    def refine_field(self, field_name, document_bytes, content_type, current_value):
        self.refined_fields.append(field_name)
        return self._refinements[field_name]


def _make_org(db_session, confidence_threshold: str = "0.80") -> Organization:
    from decimal import Decimal

    org = Organization(name="Acme Tax", confidence_threshold=Decimal(confidence_threshold))
    db_session.add(org)
    db_session.flush()
    return org


def _make_document(db_session, org: Organization, content_type="application/pdf") -> Document:
    document = Document(
        org_id=org.id,
        filename="invoice.pdf",
        content_type=content_type,
        size_bytes=len(FAKE_PDF_BYTES),
        minio_key=f"{org.id}/{uuid.uuid4()}-invoice.pdf",
        doc_type=DocumentType.invoice_or_receipt,
        status=DocumentStatus.queued,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document


def test_pipeline_raises_for_unknown_document(db_session):
    try:
        run_pipeline(uuid.uuid4(), db_session)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_fully_confident_extraction_persists_without_refinement(db_session, monkeypatch):
    monkeypatch.setattr("app.pipeline.get_object_bytes", lambda key: FAKE_PDF_BYTES)

    org = _make_org(db_session)
    document = _make_document(db_session, org)

    textract = FakeTextractClient(
        lines=["INVOICE", "Vendor Co", "TOTAL", "Bill To"],
        expense_fields=[
            ExtractedField(name="vendor", value="Vendor Co", confidence=0.95),
            ExtractedField(name="amount", value="123.45", confidence=0.95),
            ExtractedField(name="invoice_date", value="2026-01-01", confidence=0.95),
        ],
    )
    llm = FakeLLMClient(refinements={})

    result = run_pipeline(document.id, db_session, textract_client=textract, llm_client=llm)

    assert result.status == DocumentStatus.done
    assert llm.refined_fields == []  # no field was below threshold

    transaction = db_session.query(Transaction).filter_by(document_id=document.id).one()
    assert transaction.vendor == "Vendor Co"
    assert str(transaction.amount) == "123.45"
    assert transaction.transaction_date.isoformat() == "2026-01-01"
    assert transaction.review_status == ReviewStatus.ok

    extraction_results = (
        db_session.query(ExtractionResult).filter_by(document_id=document.id).all()
    )
    assert {r.field_name for r in extraction_results} == {"vendor", "amount", "invoice_date"}
    assert all(r.method == ExtractionMethod.ocr for r in extraction_results)

    audit_entries = db_session.query(AuditLogEntry).filter_by(org_id=org.id).all()
    assert any(entry.action == "document.extracted" and entry.actor == "system" for entry in audit_entries)


def test_low_confidence_field_triggers_llm_refinement_and_is_logged(db_session, monkeypatch):
    monkeypatch.setattr("app.pipeline.get_object_bytes", lambda key: FAKE_PDF_BYTES)

    org = _make_org(db_session, confidence_threshold="0.80")
    document = _make_document(db_session, org)

    textract = FakeTextractClient(
        lines=["INVOICE", "Vendor Co", "TOTAL"],
        expense_fields=[
            ExtractedField(name="vendor", value="Vndr C?", confidence=0.40),  # low confidence
            ExtractedField(name="amount", value="123.45", confidence=0.95),
            ExtractedField(name="invoice_date", value="2026-01-01", confidence=0.95),
        ],
    )
    llm = FakeLLMClient(
        refinements={"vendor": RefinedField(value="Vendor Co", confidence=0.70)}
    )

    result = run_pipeline(document.id, db_session, textract_client=textract, llm_client=llm)

    assert result.status == DocumentStatus.done
    assert llm.refined_fields == ["vendor"]  # only the low-confidence field was refined

    transaction = db_session.query(Transaction).filter_by(document_id=document.id).one()
    assert transaction.vendor == "Vendor Co"
    # LLM refined it, but 0.70 is still below the 0.80 threshold -> flagged.
    assert transaction.review_status == ReviewStatus.needs_review

    vendor_result = (
        db_session.query(ExtractionResult)
        .filter_by(document_id=document.id, field_name="vendor")
        .one()
    )
    assert vendor_result.method == ExtractionMethod.llm
    assert float(vendor_result.confidence) == 0.70

    audit_entries = db_session.query(AuditLogEntry).filter_by(org_id=org.id).all()
    extracted_entry = next(e for e in audit_entries if e.action == "document.extracted")
    assert extracted_entry.after["review_status"] == "needs_review"


def test_unknown_document_is_flagged_and_not_persisted_as_a_transaction(db_session, monkeypatch):
    monkeypatch.setattr("app.pipeline.get_object_bytes", lambda key: FAKE_PDF_BYTES)

    org = _make_org(db_session)
    document = _make_document(db_session, org)

    # No invoice/receipt or bank-statement markers at all.
    textract = FakeTextractClient(lines=["random unrelated text", "nothing recognizable"], expense_fields=[])
    llm = FakeLLMClient(refinements={})

    result = run_pipeline(document.id, db_session, textract_client=textract, llm_client=llm)

    assert result.status == DocumentStatus.needs_review
    assert textract.analyze_expense_calls == 0  # never reached ocr_extract

    assert db_session.query(Transaction).filter_by(document_id=document.id).count() == 0
    assert db_session.query(ExtractionResult).filter_by(document_id=document.id).count() == 0

    audit_entries = db_session.query(AuditLogEntry).filter_by(org_id=org.id).all()
    assert any(entry.action == "document.classification_unknown" for entry in audit_entries)
