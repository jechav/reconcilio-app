"""Pipeline-as-a-function tests for the bank-statement paths.

Everything below drives the real graph -- classify, the real CSV/OFX
parsers, the real Textract response parsing, the real schema validation and
the real persistence -- with fakes only at the three network boundaries
(object storage, Textract, the LLM). No AWS or LLM credentials are needed
or used.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extraction.types import ExtractedField, ExtractedLine
from app.models import (
    AuditLogEntry,
    Document,
    DocumentStatus,
    DocumentType,
    ExtractionMethod,
    ExtractionResult,
    Organization,
    Transaction,
    TransactionStatus,
)
from app.pipeline import PipelineDeps, run_pipeline
from tests.textract_fixtures import FakeTextractClient, textract_table_response

CSV_STATEMENT = b"""Date,Description,Amount
2026-01-04,COFFEE ROASTERS,-4.50
2026-01-06,CLIENT PAYMENT ACME,1200.00
2026-01-09,OFFICE SUPPLIES CO,-89.99
"""

OFX_STATEMENT = b"""OFXHEADER:100

<OFX><BANKTRANLIST>
<STMTTRN>
<DTPOSTED>20260104
<TRNAMT>-4.50
<NAME>COFFEE ROASTERS
</STMTTRN>
<STMTTRN>
<DTPOSTED>20260106
<TRNAMT>1200.00
<NAME>CLIENT PAYMENT
</STMTTRN>
</BANKTRANLIST></OFX>
"""


class FakeRefiner:
    """Returns canned corrections and records what it was asked to fix."""

    def __init__(self, corrections: dict[str, tuple[str, float]] | None = None) -> None:
        self.corrections = corrections or {}
        self.calls: list[tuple[int, list[str]]] = []

    def refine(self, line: ExtractedLine, field_names: list[str]) -> dict[str, ExtractedField]:
        self.calls.append((line.line_number, sorted(field_names)))
        return {
            name: ExtractedField(value=value, confidence=confidence, method=ExtractionMethod.llm)
            for name, (value, confidence) in self.corrections.items()
            if name in field_names
        }


def _deps(data: bytes = b"", textract=None, refiner=None) -> PipelineDeps:
    return PipelineDeps(
        fetch_bytes=lambda key: data,
        textract=textract or FakeTextractClient({"Blocks": []}),
        refiner=refiner or FakeRefiner(),
    )


def _make_document(db_session, filename: str, doc_type: DocumentType, threshold: float = 0.8):
    org = Organization(name="Acme Tax", confidence_threshold=threshold)
    db_session.add(org)
    db_session.flush()

    document = Document(
        org_id=org.id,
        filename=filename,
        content_type="application/octet-stream",
        size_bytes=1024,
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


def _extraction_results(db_session, document) -> list[ExtractionResult]:
    return list(
        db_session.execute(
            select(ExtractionResult)
            .where(ExtractionResult.document_id == document.id)
            .order_by(ExtractionResult.line_number)
        ).scalars()
    )


def _audit_actions(db_session, org_id) -> list[str]:
    return list(
        db_session.execute(
            select(AuditLogEntry.action).where(AuditLogEntry.org_id == org_id)
        ).scalars()
    )


# --- structured parse: CSV -------------------------------------------------


def test_csv_statement_produces_one_transaction_per_line_without_touching_textract(db_session):
    document = _make_document(db_session, "january.csv", DocumentType.bank_statement)
    textract = FakeTextractClient({"Blocks": []})

    result = run_pipeline(document.id, db_session, _deps(CSV_STATEMENT, textract=textract))

    assert textract.calls == []  # structured formats skip OCR entirely
    transactions = _transactions(db_session, document)
    assert len(transactions) == 3
    assert [t.amount for t in transactions] == [
        Decimal("-4.50"),
        Decimal("1200.00"),
        Decimal("-89.99"),
    ]
    assert transactions[0].description == "COFFEE ROASTERS"
    assert all(t.status == TransactionStatus.resolved for t in transactions)
    assert result.status == DocumentStatus.done


def test_csv_statement_records_structured_parse_provenance_per_line(db_session):
    document = _make_document(db_session, "january.csv", DocumentType.bank_statement)

    run_pipeline(document.id, db_session, _deps(CSV_STATEMENT))

    results = _extraction_results(db_session, document)
    assert len(results) == 3
    for extraction in results:
        assert extraction.method == ExtractionMethod.structured_parse
        assert extraction.confidence == 1.0
        assert extraction.transaction_id is not None
        assert set(extraction.fields) == {"date", "description", "amount"}
        for field in extraction.fields.values():
            assert field["method"] == ExtractionMethod.structured_parse.value
            assert field["confidence"] == 1.0


def test_extraction_writes_audit_log_entries_on_the_structured_path(db_session):
    document = _make_document(db_session, "january.csv", DocumentType.bank_statement)

    run_pipeline(document.id, db_session, _deps(CSV_STATEMENT))

    actions = _audit_actions(db_session, document.org_id)
    assert actions.count("transaction.extracted") == 3
    assert actions.count("document.extracted") == 1

    entry = db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.entity_id == document.id)
    ).scalars().one()
    assert entry.actor == "system"
    assert entry.after["path"] == "structured"
    assert entry.after["transactions_created"] == 3


# --- structured parse: OFX -------------------------------------------------


def test_ofx_statement_produces_one_transaction_per_line(db_session):
    document = _make_document(db_session, "january.ofx", DocumentType.bank_statement)

    result = run_pipeline(document.id, db_session, _deps(OFX_STATEMENT))

    transactions = _transactions(db_session, document)
    assert len(transactions) == 2
    assert [str(t.txn_date) for t in transactions] == ["2026-01-04", "2026-01-06"]
    assert [t.amount for t in transactions] == [Decimal("-4.50"), Decimal("1200.00")]
    results = _extraction_results(db_session, document)
    assert [r.method for r in results] == [ExtractionMethod.structured_parse] * 2
    assert all(r.confidence == 1.0 for r in results)
    assert result.status == DocumentStatus.done


# --- OCR path --------------------------------------------------------------


def _pdf_textract(low_confidence_amount: bool = False) -> FakeTextractClient:
    amount_confidence = 55.0 if low_confidence_amount else 96.0
    return FakeTextractClient(
        textract_table_response(
            [
                [("Date", 99.0), ("Description", 99.0), ("Amount", 99.0)],
                [("2026-01-04", 98.0), ("COFFEE ROASTERS", 97.0), ("-4.50", amount_confidence)],
                [("2026-01-06", 99.0), ("CLIENT PAYMENT", 96.0), ("1200.00", 95.0)],
                [("2026-01-09", 97.0), ("OFFICE SUPPLIES", 94.0), ("-89.99", 93.0)],
            ]
        )
    )


def test_pdf_statement_produces_one_transaction_per_statement_line(db_session):
    document = _make_document(db_session, "january.pdf", DocumentType.bank_statement)
    refiner = FakeRefiner()

    result = run_pipeline(
        document.id, db_session, _deps(b"%PDF-1.4", textract=_pdf_textract(), refiner=refiner)
    )

    transactions = _transactions(db_session, document)
    assert len(transactions) == 3
    assert [t.amount for t in transactions] == [
        Decimal("-4.50"),
        Decimal("1200.00"),
        Decimal("-89.99"),
    ]
    assert refiner.calls == []  # nothing was below the threshold
    results = _extraction_results(db_session, document)
    assert [r.method for r in results] == [ExtractionMethod.ocr] * 3
    assert results[0].confidence == pytest.approx(0.96)
    assert result.status == DocumentStatus.done


def test_low_confidence_field_is_refined_by_the_llm_and_marked_as_such(db_session):
    document = _make_document(db_session, "january.pdf", DocumentType.bank_statement)
    refiner = FakeRefiner({"amount": ("-45.00", 0.97)})

    result = run_pipeline(
        document.id,
        db_session,
        _deps(b"%PDF-1.4", textract=_pdf_textract(low_confidence_amount=True), refiner=refiner),
    )

    assert refiner.calls == [(1, ["amount"])]  # only the weak field, only that line
    transactions = _transactions(db_session, document)
    assert transactions[0].amount == Decimal("-45.00")
    assert transactions[0].status == TransactionStatus.resolved

    refined = _extraction_results(db_session, document)[0]
    assert refined.fields["amount"]["method"] == ExtractionMethod.llm.value
    assert refined.fields["date"]["method"] == ExtractionMethod.ocr.value
    # The line-level summary tracks the weakest field, which after a
    # confident refinement is the OCR-read description (0.97), not the amount.
    assert refined.method == ExtractionMethod.ocr
    assert refined.confidence == pytest.approx(0.97)
    assert result.status == DocumentStatus.done


def test_field_still_low_after_refinement_flags_the_transaction_and_the_document(db_session):
    document = _make_document(db_session, "january.pdf", DocumentType.bank_statement)
    refiner = FakeRefiner({"amount": ("-4.50", 0.4)})  # the model was not sure either

    result = run_pipeline(
        document.id,
        db_session,
        _deps(b"%PDF-1.4", textract=_pdf_textract(low_confidence_amount=True), refiner=refiner),
    )

    transactions = _transactions(db_session, document)
    assert transactions[0].status == TransactionStatus.needs_review
    assert [t.status for t in transactions[1:]] == [TransactionStatus.resolved] * 2
    # One unresolved line is enough to pull the whole Document to review.
    assert result.status == DocumentStatus.needs_review


def test_ocr_path_writes_audit_entries_including_the_refined_lines(db_session):
    document = _make_document(db_session, "january.pdf", DocumentType.bank_statement)
    refiner = FakeRefiner({"amount": ("-45.00", 0.97)})

    run_pipeline(
        document.id,
        db_session,
        _deps(b"%PDF-1.4", textract=_pdf_textract(low_confidence_amount=True), refiner=refiner),
    )

    entry = db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.entity_id == document.id)
    ).scalars().one()
    assert entry.after["path"] == "ocr"
    assert entry.after["refined_lines"] == [1]
    assert _audit_actions(db_session, document.org_id).count("transaction.extracted") == 3


def test_document_fails_when_textract_finds_no_statement_table(db_session):
    document = _make_document(db_session, "january.pdf", DocumentType.bank_statement)
    textract = FakeTextractClient(textract_table_response([[("Item", 99.0)], [("Widget", 99.0)]]))

    with pytest.raises(Exception):
        run_pipeline(document.id, db_session, _deps(b"%PDF-1.4", textract=textract))

    db_session.rollback()
    assert db_session.get(Document, document.id).status == DocumentStatus.failed


# --- validation and thresholds --------------------------------------------


def test_a_malformed_line_is_rejected_while_its_neighbours_still_persist(db_session):
    document = _make_document(db_session, "january.csv", DocumentType.bank_statement)
    statement = b"Date,Description,Amount\nnot-a-date,COFFEE,-4.50\n2026-01-06,PAYMENT,1200.00\n"

    run_pipeline(document.id, db_session, _deps(statement))

    transactions = _transactions(db_session, document)
    assert [t.description for t in transactions] == ["PAYMENT"]

    entry = db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.entity_id == document.id)
    ).scalars().one()
    assert entry.after["rejected_lines"][0]["line_number"] == 1
    assert entry.after["rejected_lines"][0]["error"]


def test_document_fails_when_no_line_passes_validation(db_session):
    document = _make_document(db_session, "january.csv", DocumentType.bank_statement)
    statement = b"Date,Description,Amount\nnot-a-date,COFFEE,not-an-amount\n"

    with pytest.raises(Exception):
        run_pipeline(document.id, db_session, _deps(statement))

    db_session.rollback()
    assert db_session.get(Document, document.id).status == DocumentStatus.failed
    assert _transactions(db_session, document) == []


def test_the_organizations_configured_threshold_is_what_decides_refinement(db_session):
    document = _make_document(db_session, "january.pdf", DocumentType.bank_statement, threshold=0.5)
    refiner = FakeRefiner({"amount": ("-45.00", 0.99)})

    result = run_pipeline(
        document.id,
        db_session,
        _deps(b"%PDF-1.4", textract=_pdf_textract(low_confidence_amount=True), refiner=refiner),
    )

    # 0.55 clears a 0.5 threshold, so no refinement and no review flag.
    assert refiner.calls == []
    assert _transactions(db_session, document)[0].amount == Decimal("-4.50")
    assert result.status == DocumentStatus.done
