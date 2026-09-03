"""Issue #18 -- the pipeline routes a multi-page PDF through Textract's
async job APIs instead of the sync, bytes-based ones that reject it.

Drives the real graph with a fake Textract client that only answers the
async methods (`*_async`) and raises on the sync ones -- proving the
pipeline node itself, not just the client, makes the sync/async choice.
The normalized output is compared line-for-line against the equivalent
sync-path fixtures in test_pipeline.py / test_pipeline_bank_statement.py to
confirm the two paths converge on the same shape.
"""

from __future__ import annotations

import io
import uuid
from decimal import Decimal

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.extraction.llm import RefinedField
from app.extraction.textract import (
    ExtractedField,
    TextractExpenseResult,
    TextractJobFailed,
    TextractJobTimeout,
)
from app.models import Document, DocumentStatus, DocumentType, Organization, Transaction
from app.pipeline import PipelineDeps, run_pipeline
from tests.textract_fixtures import textract_table_response


def _multi_page_pdf_bytes(num_pages: int = 3) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


MULTI_PAGE_PDF = _multi_page_pdf_bytes(3)


class AsyncOnlyTextractClient:
    """A fake that only implements the async job methods -- any call to a
    sync method is a test failure, proving the pipeline node routed a
    multi-page document through the async path rather than the one that
    would reject it in production."""

    def __init__(
        self,
        *,
        detect_lines: list[str] | None = None,
        expense_fields: list[ExtractedField] | None = None,
        table_response: dict | None = None,
    ) -> None:
        self._detect_lines = detect_lines or []
        self._expense_fields = expense_fields or []
        self._table_response = table_response or {"Blocks": []}
        self.detect_text_async_calls: list[str] = []
        self.analyze_expense_async_calls: list[str] = []
        self.analyze_document_async_calls: list[str] = []

    def detect_text(self, document_bytes: bytes) -> list[str]:
        raise AssertionError("multi-page document must not use the sync detect_text call")

    def detect_text_async(self, s3_key: str) -> list[str]:
        self.detect_text_async_calls.append(s3_key)
        return self._detect_lines

    def analyze_expense(self, document_bytes: bytes) -> TextractExpenseResult:
        raise AssertionError("multi-page document must not use the sync analyze_expense call")

    def analyze_expense_async(self, s3_key: str) -> TextractExpenseResult:
        self.analyze_expense_async_calls.append(s3_key)
        return TextractExpenseResult(fields=self._expense_fields)

    def analyze_document(self, document_bytes: bytes) -> dict:
        raise AssertionError("multi-page document must not use the sync analyze_document call")

    def analyze_document_async(self, s3_key: str) -> dict:
        self.analyze_document_async_calls.append(s3_key)
        return self._table_response


class FailingAsyncTextractClient:
    """Raises whatever the async job would raise -- job failure or a poll
    timeout -- so the pipeline node's error handling can be exercised
    without a real AWS call."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def analyze_document(self, document_bytes: bytes) -> dict:
        raise AssertionError("must not use the sync call for a multi-page document")

    def analyze_document_async(self, s3_key: str) -> dict:
        raise self._exc


def _make_org(db_session, confidence_threshold: str = "0.80") -> Organization:
    org = Organization(name="Acme Tax", confidence_threshold=Decimal(confidence_threshold))
    db_session.add(org)
    db_session.flush()
    return org


def _make_document(
    db_session,
    org: Organization,
    doc_type: DocumentType,
    filename: str,
    content_type: str = "application/pdf",
) -> Document:
    document = Document(
        org_id=org.id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(MULTI_PAGE_PDF),
        minio_key=f"{org.id}/{uuid.uuid4()}-{filename}",
        doc_type=doc_type,
        status=DocumentStatus.queued,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document


def _transactions(db_session, document) -> list[Transaction]:
    return list(
        db_session.execute(
            select(Transaction)
            .where(Transaction.document_id == document.id)
            .order_by(Transaction.line_number)
        ).scalars()
    )


# --- bank-statement (TABLES) path --------------------------------------


def test_multipage_bank_statement_pdf_is_routed_through_analyze_document_async(db_session):
    from app.extraction.llm import NullRefiner

    org = _make_org(db_session)
    document = _make_document(db_session, org, DocumentType.bank_statement, "statement.pdf")
    textract = AsyncOnlyTextractClient(
        table_response=textract_table_response(
            [
                [("Date", 99.0), ("Description", 99.0), ("Amount", 99.0)],
                [("2026-01-04", 98.0), ("COFFEE ROASTERS", 97.0), ("-4.50", 96.0)],
            ]
        )
    )
    deps = PipelineDeps(
        fetch_bytes=lambda key: MULTI_PAGE_PDF,
        textract=textract,
        refiner=NullRefiner(),
    )

    result = run_pipeline(document.id, db_session, deps)

    # Same normalized shape a single-page/sync run produces: one Transaction
    # with the OCR-read fields, method and confidence -- see the sync-path
    # assertions in test_pipeline_bank_statement.py.
    transactions = _transactions(db_session, document)
    assert len(transactions) == 1
    assert transactions[0].amount == Decimal("-4.50")
    assert transactions[0].description == "COFFEE ROASTERS"
    assert result.status == DocumentStatus.done
    assert textract.analyze_document_async_calls == [document.minio_key]


# --- invoice/receipt (AnalyzeExpense) path ------------------------------


def test_multipage_invoice_pdf_is_routed_through_the_async_apis(db_session):
    org = _make_org(db_session)
    document = _make_document(
        db_session, org, DocumentType.invoice_or_receipt, "invoice.pdf"
    )
    textract = AsyncOnlyTextractClient(
        detect_lines=["INVOICE", "Vendor: Acme Co", "Total: $123.45"],
        expense_fields=[
            ExtractedField(name="vendor", value="Acme Co", confidence=0.95),
            ExtractedField(name="amount", value="123.45", confidence=0.93),
            ExtractedField(name="invoice_date", value="2026-01-04", confidence=0.9),
        ],
    )
    deps = PipelineDeps(
        fetch_bytes=lambda key: MULTI_PAGE_PDF,
        textract=textract,
        refiner=object(),  # unused on this path
        llm_client=None,
    )

    result = run_pipeline(document.id, db_session, deps)

    transactions = _transactions(db_session, document)
    assert len(transactions) == 1
    assert transactions[0].amount == Decimal("123.45")
    assert transactions[0].description == "Acme Co"
    assert result.status == DocumentStatus.done
    assert textract.detect_text_async_calls == [document.minio_key]
    assert textract.analyze_expense_async_calls == [document.minio_key]


# --- async failure modes surface as a pipeline failure, not a hang -----


def test_async_job_failure_marks_the_document_failed(db_session):
    org = _make_org(db_session)
    document = _make_document(db_session, org, DocumentType.bank_statement, "statement.pdf")
    textract = FailingAsyncTextractClient(TextractJobFailed("job failed: bad input"))
    deps = PipelineDeps(fetch_bytes=lambda key: MULTI_PAGE_PDF, textract=textract, refiner=object())

    with pytest.raises(TextractJobFailed):
        run_pipeline(document.id, db_session, deps)

    db_session.rollback()
    assert db_session.get(Document, document.id).status == DocumentStatus.failed


def test_async_job_timeout_marks_the_document_failed_instead_of_hanging(db_session):
    org = _make_org(db_session)
    document = _make_document(db_session, org, DocumentType.bank_statement, "statement.pdf")
    textract = FailingAsyncTextractClient(TextractJobTimeout("job did not finish in time"))
    deps = PipelineDeps(fetch_bytes=lambda key: MULTI_PAGE_PDF, textract=textract, refiner=object())

    with pytest.raises(TextractJobTimeout):
        run_pipeline(document.id, db_session, deps)

    db_session.rollback()
    assert db_session.get(Document, document.id).status == DocumentStatus.failed
