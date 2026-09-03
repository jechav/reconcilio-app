"""Pipeline-as-a-function tests for the Category suggestion step (issue #5).

Drives the real graph end to end (classify -> extract -> validate ->
categorize -> persist) with the classifier -- the only new network boundary
this ticket adds -- faked, alongside the existing Textract/LLM fakes.
"""

import uuid
from decimal import Decimal

from app.extraction.categorize import CategoryClassifier, CategorySuggestion, CorrectionExample
from app.extraction.llm import NullRefiner
from app.extraction.textract import ExtractedField, TextractExpenseResult
from app.models import (
    AuditLogEntry,
    Category,
    CategoryCorrection,
    Document,
    DocumentStatus,
    DocumentType,
    Organization,
    Transaction,
    TransactionStatus,
)
from app.pipeline import PipelineDeps, run_pipeline

FAKE_PDF_BYTES = b"%PDF-1.4 fake invoice bytes for testing"


class FakeTextractClient:
    def __init__(self, lines, expense_fields) -> None:
        self._lines = lines
        self._expense_fields = expense_fields

    def detect_text(self, document_bytes):
        return self._lines

    def analyze_expense(self, document_bytes):
        return TextractExpenseResult(fields=self._expense_fields)

    def analyze_document(self, document_bytes):  # pragma: no cover
        raise AssertionError("invoice/receipt path never calls analyze_document")


class FakeClassifier:
    """Records exactly what it was asked to categorize -- the LLM call is
    mocked, never a real network request."""

    def __init__(self, suggestion: CategorySuggestion) -> None:
        self._suggestion = suggestion
        self.calls: list[dict] = []

    def suggest(self, description, amount, category_names, examples):
        self.calls.append(
            {
                "description": description,
                "amount": amount,
                "category_names": list(category_names),
                "examples": list(examples),
            }
        )
        return self._suggestion


def _make_org(db_session) -> Organization:
    org = Organization(name="Acme Tax")
    db_session.add(org)
    db_session.flush()
    return org


def _make_categories(db_session, org: Organization, names: list[str]) -> dict[str, Category]:
    by_name = {}
    for name in names:
        category = Category(org_id=org.id, name=name)
        db_session.add(category)
        by_name[name] = category
    db_session.flush()
    return by_name


def _make_corrected_transaction(db_session, org: Organization, category: Category, description: str) -> Transaction:
    """A Transaction with a prior correction recorded against it -- only the
    correction (not the Transaction itself) matters to these tests, but
    CategoryCorrection.transaction_id is a real foreign key."""
    import datetime
    from decimal import Decimal

    document = Document(
        org_id=org.id,
        filename="statement.csv",
        content_type="text/csv",
        size_bytes=1,
        minio_key=f"{org.id}/{uuid.uuid4()}-statement.csv",
        doc_type=DocumentType.bank_statement,
        status=DocumentStatus.done,
    )
    db_session.add(document)
    db_session.flush()
    transaction = Transaction(
        org_id=org.id,
        document_id=document.id,
        line_number=1,
        txn_date=datetime.date(2026, 1, 1),
        description=description,
        amount=Decimal("-1.00"),
        confidence=1.0,
        status=TransactionStatus.resolved,
        category_id=category.id,
        category_confidence=1.0,
    )
    db_session.add(transaction)
    db_session.flush()
    return transaction


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


def _deps(textract, classifier: CategoryClassifier) -> PipelineDeps:
    return PipelineDeps(
        fetch_bytes=lambda key: FAKE_PDF_BYTES,
        textract=textract,
        refiner=NullRefiner(),
        llm_client=None,
        classifier=classifier,
    )


def test_transaction_gets_exactly_one_suggested_category_with_confidence(db_session):
    org = _make_org(db_session)
    categories = _make_categories(db_session, org, ["Travel", "Meals", "Other"])
    document = _make_document(db_session, org)

    textract = FakeTextractClient(
        lines=["INVOICE", "Airline Co", "TOTAL"],
        expense_fields=[
            ExtractedField(name="vendor", value="Airline Co", confidence=0.95),
            ExtractedField(name="amount", value="250.00", confidence=0.95),
            ExtractedField(name="invoice_date", value="2026-01-01", confidence=0.95),
        ],
    )
    classifier = FakeClassifier(CategorySuggestion(category_name="Travel", confidence=0.87))

    run_pipeline(document.id, db_session, _deps(textract, classifier))

    transaction = db_session.query(Transaction).filter_by(document_id=document.id).one()
    assert transaction.category_id == categories["Travel"].id
    assert transaction.category_confidence == 0.87

    # The classifier was handed this Organization's own Category names.
    assert len(classifier.calls) == 1
    assert set(classifier.calls[0]["category_names"]) == {"Travel", "Meals", "Other"}
    assert classifier.calls[0]["description"] == "Airline Co"

    audit_entries = db_session.query(AuditLogEntry).filter_by(org_id=org.id).all()
    suggestion_entry = next(e for e in audit_entries if e.action == "transaction.category_suggested")
    assert suggestion_entry.actor == "system"
    assert suggestion_entry.before is None
    assert suggestion_entry.after["category_id"] == str(categories["Travel"].id)
    assert suggestion_entry.after["confidence"] == 0.87


def test_past_corrections_are_passed_as_org_scoped_few_shot_examples(db_session):
    org = _make_org(db_session)
    other_org = _make_org(db_session)
    categories = _make_categories(db_session, org, ["Travel", "Meals", "Other"])
    _make_categories(db_session, other_org, ["Travel", "Meals", "Other"])

    # A correction on this org should be surfaced as a few-shot example...
    same_org_txn = _make_corrected_transaction(db_session, org, categories["Travel"], "Ride share to client site")
    db_session.add(
        CategoryCorrection(
            org_id=org.id,
            transaction_id=same_org_txn.id,
            description="Ride share to client site",
            category_id=categories["Travel"].id,
        )
    )
    # ...but a correction belonging to a *different* Organization must never
    # leak into this Organization's suggestions (AC5: org-scoped only).
    other_travel = db_session.query(Category).filter_by(org_id=other_org.id, name="Travel").one()
    other_org_txn = _make_corrected_transaction(db_session, other_org, other_travel, "Should never appear")
    db_session.add(
        CategoryCorrection(
            org_id=other_org.id,
            transaction_id=other_org_txn.id,
            description="Should never appear",
            category_id=other_travel.id,
        )
    )
    db_session.commit()

    document = _make_document(db_session, org)
    textract = FakeTextractClient(
        lines=["INVOICE", "Cab Co", "TOTAL"],
        expense_fields=[
            ExtractedField(name="vendor", value="Cab Co", confidence=0.95),
            ExtractedField(name="amount", value="40.00", confidence=0.95),
            ExtractedField(name="invoice_date", value="2026-01-02", confidence=0.95),
        ],
    )
    classifier = FakeClassifier(CategorySuggestion(category_name="Travel", confidence=0.9))

    run_pipeline(document.id, db_session, _deps(textract, classifier))

    assert len(classifier.calls) == 1
    examples = classifier.calls[0]["examples"]
    assert examples == [CorrectionExample(description="Ride share to client site", category_name="Travel")]


def test_classifier_hallucinating_an_unknown_category_leaves_transaction_unassigned(db_session):
    org = _make_org(db_session)
    _make_categories(db_session, org, ["Travel", "Meals"])
    document = _make_document(db_session, org)

    textract = FakeTextractClient(
        lines=["INVOICE", "Vendor Co", "TOTAL"],
        expense_fields=[
            ExtractedField(name="vendor", value="Vendor Co", confidence=0.95),
            ExtractedField(name="amount", value="10.00", confidence=0.95),
            ExtractedField(name="invoice_date", value="2026-01-01", confidence=0.95),
        ],
    )
    # A category name that doesn't exist in this org's list -- the endpoint
    # should never persist a nonexistent Category id.
    classifier = FakeClassifier(CategorySuggestion(category_name="Not A Real Category", confidence=0.5))

    run_pipeline(document.id, db_session, _deps(textract, classifier))

    transaction = db_session.query(Transaction).filter_by(document_id=document.id).one()
    assert transaction.category_id is None
