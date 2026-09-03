"""Pipeline-as-a-function tests for reconciliation matching (issue #6).

Covers a clean auto-match, a tied-candidate case, an unmatched item on each
side, and manual match/unmatch through the API boundary. Transactions are
created directly against the database (no need to drive the extraction
pipeline itself -- that's covered by test_pipeline*.py) since reconciliation
only cares about persisted Transaction/Document rows.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models import (
    AuditLogEntry,
    Document,
    DocumentStatus,
    DocumentType,
    MatchType,
    Organization,
    ReconciliationMatch,
    Transaction,
    TransactionStatus,
)
from app.reconciliation import (
    ReconciliationError,
    create_manual_match,
    remove_manual_match,
    run_reconciliation_for_document,
)


def _make_org(db_session) -> Organization:
    org = Organization(name="Acme Tax")
    db_session.add(org)
    db_session.flush()
    return org


def _make_document(db_session, org: Organization, doc_type: DocumentType, filename: str = "doc") -> Document:
    document = Document(
        org_id=org.id,
        filename=filename,
        content_type="text/csv" if doc_type == DocumentType.bank_statement else "application/pdf",
        size_bytes=10,
        minio_key=f"{org.id}/{uuid.uuid4()}-{filename}",
        doc_type=doc_type,
        status=DocumentStatus.processing,
    )
    db_session.add(document)
    db_session.flush()
    return document


def _make_transaction(
    db_session,
    org: Organization,
    document: Document,
    *,
    line_number: int = 1,
    txn_date: date,
    description: str,
    amount: str,
    status: TransactionStatus = TransactionStatus.resolved,
) -> Transaction:
    transaction = Transaction(
        org_id=org.id,
        document_id=document.id,
        line_number=line_number,
        txn_date=txn_date,
        description=description,
        amount=Decimal(amount),
        confidence=0.95,
        status=status,
    )
    db_session.add(transaction)
    db_session.flush()
    return transaction


def test_clean_auto_match_within_amount_and_date_tolerance(db_session):
    org = _make_org(db_session)
    expense_doc = _make_document(db_session, org, DocumentType.invoice_or_receipt, "invoice.pdf")
    expense_txn = _make_transaction(
        db_session, org, expense_doc, txn_date=date(2026, 1, 1), description="Vendor Co", amount="123.45"
    )
    bank_doc = _make_document(db_session, org, DocumentType.bank_statement, "statement.csv")
    bank_txn = _make_transaction(
        db_session, org, bank_doc, txn_date=date(2026, 1, 3), description="VENDOR CO PMT", amount="-123.45"
    )
    db_session.commit()

    created = run_reconciliation_for_document(bank_doc.id, db_session)

    assert len(created) == 1
    match = created[0]
    assert match.bank_transaction_id == bank_txn.id
    assert match.expense_transaction_id == expense_txn.id
    assert match.match_type == MatchType.automatic
    assert match.actor == "system"
    assert match.confidence == pytest.approx(0.95)

    stored = db_session.query(ReconciliationMatch).one()
    assert stored.id == match.id

    audit = (
        db_session.query(AuditLogEntry)
        .filter_by(entity_type="reconciliation_match", action="reconciliation_match.created")
        .one()
    )
    assert audit.actor == "system"
    assert audit.after["tied_candidates"] == 1


def test_amount_outside_one_dollar_tolerance_does_not_match(db_session):
    org = _make_org(db_session)
    expense_doc = _make_document(db_session, org, DocumentType.invoice_or_receipt, "invoice.pdf")
    _make_transaction(
        db_session, org, expense_doc, txn_date=date(2026, 1, 1), description="Vendor Co", amount="123.45"
    )
    bank_doc = _make_document(db_session, org, DocumentType.bank_statement, "statement.csv")
    _make_transaction(
        db_session, org, bank_doc, txn_date=date(2026, 1, 2), description="VENDOR CO PMT", amount="-125.00"
    )
    db_session.commit()

    created = run_reconciliation_for_document(bank_doc.id, db_session)

    assert created == []
    assert db_session.query(ReconciliationMatch).count() == 0


def test_tied_candidates_are_broken_by_closest_date_and_flagged_lower_confidence(db_session):
    org = _make_org(db_session)
    expense_doc = _make_document(db_session, org, DocumentType.invoice_or_receipt, "invoice.pdf")
    close_expense = _make_transaction(
        db_session,
        org,
        expense_doc,
        line_number=1,
        txn_date=date(2026, 1, 2),
        description="Vendor Co",
        amount="100.00",
    )
    far_expense = _make_transaction(
        db_session,
        org,
        expense_doc,
        line_number=2,
        txn_date=date(2026, 1, 5),
        description="Vendor Co Two",
        amount="100.50",
    )
    bank_doc = _make_document(db_session, org, DocumentType.bank_statement, "statement.csv")
    bank_txn = _make_transaction(
        db_session, org, bank_doc, txn_date=date(2026, 1, 1), description="VENDOR CO PMT", amount="-100.00"
    )
    db_session.commit()

    created = run_reconciliation_for_document(bank_doc.id, db_session)

    assert len(created) == 1
    match = created[0]
    assert match.bank_transaction_id == bank_txn.id
    assert match.expense_transaction_id == close_expense.id  # closer date wins
    assert match.confidence == pytest.approx(0.6)  # flagged lower-confidence

    # The losing candidate stays unmatched, not silently claimed.
    db_session.expire_all()
    assert far_expense.id not in {
        m.expense_transaction_id for m in db_session.query(ReconciliationMatch).all()
    }


def test_unmatched_items_on_each_side_stay_unmatched(db_session):
    org = _make_org(db_session)
    expense_doc = _make_document(db_session, org, DocumentType.invoice_or_receipt, "invoice.pdf")
    lonely_expense = _make_transaction(
        db_session, org, expense_doc, txn_date=date(2026, 1, 1), description="Vendor Co", amount="50.00"
    )
    bank_doc = _make_document(db_session, org, DocumentType.bank_statement, "statement.csv")
    lonely_bank = _make_transaction(
        db_session, org, bank_doc, txn_date=date(2026, 2, 20), description="UNRELATED CHARGE", amount="-999.00"
    )
    db_session.commit()

    created_expense_side = run_reconciliation_for_document(expense_doc.id, db_session)
    created_bank_side = run_reconciliation_for_document(bank_doc.id, db_session)

    assert created_expense_side == []
    assert created_bank_side == []
    assert db_session.query(ReconciliationMatch).count() == 0
    # both remain independently visible/queryable, not swallowed
    assert lonely_expense.id is not None
    assert lonely_bank.id is not None


def test_manual_match_bypasses_algorithmic_criteria_and_is_audited(db_session):
    org = _make_org(db_session)
    expense_doc = _make_document(db_session, org, DocumentType.invoice_or_receipt, "invoice.pdf")
    expense_txn = _make_transaction(
        db_session, org, expense_doc, txn_date=date(2026, 1, 1), description="Vendor Co", amount="500.00"
    )
    bank_doc = _make_document(db_session, org, DocumentType.bank_statement, "statement.csv")
    # Wildly outside amount/date tolerance -- a manual match must still work.
    bank_txn = _make_transaction(
        db_session, org, bank_doc, txn_date=date(2026, 6, 1), description="SOMETHING ELSE", amount="-1.00"
    )
    db_session.commit()

    match = create_manual_match(
        db_session,
        org_id=org.id,
        bank_transaction_id=bank_txn.id,
        expense_transaction_id=expense_txn.id,
        actor=str(uuid.uuid4()),
    )

    assert match.match_type == MatchType.manual
    assert match.confidence == pytest.approx(1.0)
    audit = (
        db_session.query(AuditLogEntry)
        .filter_by(entity_type="reconciliation_match", action="reconciliation_match.created")
        .one()
    )
    assert audit.actor == match.actor

    # One-to-one still enforced: a second manual match onto either leg fails.
    other_expense = _make_transaction(
        db_session, org, expense_doc, line_number=2, txn_date=date(2026, 1, 1), description="Other", amount="1.00"
    )
    db_session.commit()
    with pytest.raises(ReconciliationError):
        create_manual_match(
            db_session,
            org_id=org.id,
            bank_transaction_id=bank_txn.id,
            expense_transaction_id=other_expense.id,
            actor=str(uuid.uuid4()),
        )

    actor = str(uuid.uuid4())
    remove_manual_match(db_session, org_id=org.id, match_id=match.id, actor=actor)

    assert db_session.query(ReconciliationMatch).count() == 0
    removal_audit = (
        db_session.query(AuditLogEntry)
        .filter_by(entity_type="reconciliation_match", action="reconciliation_match.removed")
        .one()
    )
    assert removal_audit.actor == actor
    assert removal_audit.before["bank_transaction_id"] == str(bank_txn.id)


def test_pipeline_run_triggers_reconciliation_for_new_document(db_session):
    """Whenever a Document finishes extraction, matching runs incrementally
    (issue #6, AC1) -- exercised here through the pipeline entry point
    rather than run_reconciliation_for_document directly."""
    from app.extraction.llm import NullRefiner
    from app.pipeline import PipelineDeps, run_pipeline
    from tests.textract_fixtures import FakeTextractClient

    org = _make_org(db_session)
    expense_doc = _make_document(db_session, org, DocumentType.invoice_or_receipt, "invoice.pdf")
    expense_txn = _make_transaction(
        db_session, org, expense_doc, txn_date=date(2026, 1, 1), description="Coffee Roasters", amount="4.50"
    )

    bank_doc = Document(
        org_id=org.id,
        filename="statement.csv",
        content_type="text/csv",
        size_bytes=10,
        minio_key=f"{org.id}/{uuid.uuid4()}-statement.csv",
        doc_type=DocumentType.bank_statement,
        status=DocumentStatus.queued,
    )
    db_session.add(bank_doc)
    db_session.commit()
    db_session.refresh(bank_doc)

    csv_bytes = b"Date,Description,Amount\n2026-01-02,COFFEE ROASTERS,-4.50\n"
    deps = PipelineDeps(
        fetch_bytes=lambda key: csv_bytes,
        textract=FakeTextractClient({"Blocks": []}),
        refiner=NullRefiner(),
    )

    result = run_pipeline(bank_doc.id, db_session, deps)

    assert result.status == DocumentStatus.done
    matches = db_session.query(ReconciliationMatch).all()
    assert len(matches) == 1
    assert matches[0].expense_transaction_id == expense_txn.id
