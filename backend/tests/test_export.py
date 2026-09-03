"""API-boundary tests for the Transaction export endpoint (issue #9)."""

import csv
import io
import uuid
from datetime import date
from decimal import Decimal

from app.models import (
    Document,
    DocumentStatus,
    DocumentType,
    MatchType,
    ReconciliationMatch,
    Transaction,
    TransactionStatus,
)


def _signup(client, email, org_name="Acme Tax"):
    response = client.post(
        "/auth/signup",
        json={"email": email, "password": "correct-horse", "org_name": org_name},
    )
    assert response.status_code == 201
    return response.json()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _make_document(db_session, org_id, doc_type, filename):
    document = Document(
        org_id=org_id,
        filename=filename,
        content_type="text/csv" if doc_type == DocumentType.bank_statement else "application/pdf",
        size_bytes=10,
        minio_key=f"{org_id}/{uuid.uuid4()}-{filename}",
        doc_type=doc_type,
        status=DocumentStatus.processing,
    )
    db_session.add(document)
    db_session.flush()
    return document


def _make_transaction(
    db_session,
    org_id,
    document,
    *,
    line_number=1,
    txn_date,
    description,
    amount,
    category_id=None,
    status=TransactionStatus.resolved,
):
    transaction = Transaction(
        org_id=org_id,
        document_id=document.id,
        line_number=line_number,
        txn_date=txn_date,
        description=description,
        amount=Decimal(amount),
        confidence=0.95,
        status=status,
        category_id=category_id,
    )
    db_session.add(transaction)
    db_session.flush()
    return transaction


def _get_category_id(client, headers, name):
    response = client.get("/categories", headers=headers)
    for category in response.json():
        if category["name"] == name:
            return category["id"]
    raise AssertionError(f"category {name} not found")


def _setup_mixed_transactions(client, db_session, unique_email):
    """One org with four Transactions covering every combination of
    categorized/uncategorized and matched/unmatched, plus a bank Transaction
    outside the export's date range."""
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])
    travel_id = uuid.UUID(_get_category_id(client, headers, "Travel"))

    expense_doc = _make_document(db_session, org_id, DocumentType.invoice_or_receipt, "invoice.pdf")
    bank_doc = _make_document(db_session, org_id, DocumentType.bank_statement, "statement.csv")

    # Categorized + matched.
    matched_expense = _make_transaction(
        db_session,
        org_id,
        expense_doc,
        line_number=1,
        txn_date=date(2026, 3, 2),
        description="Vendor Co",
        amount="-500.00",
        category_id=travel_id,
    )
    matched_bank = _make_transaction(
        db_session,
        org_id,
        bank_doc,
        line_number=1,
        txn_date=date(2026, 3, 4),
        description="Vendor Co",
        amount="-500.00",
        category_id=travel_id,
    )
    db_session.add(
        ReconciliationMatch(
            org_id=org_id,
            bank_transaction_id=matched_bank.id,
            expense_transaction_id=matched_expense.id,
            match_type=MatchType.automatic,
            confidence=0.95,
            actor="system",
        )
    )

    # Uncategorized + unmatched, still needs review.
    unreviewed = _make_transaction(
        db_session,
        org_id,
        expense_doc,
        line_number=2,
        txn_date=date(2026, 3, 10),
        description="Unknown Vendor",
        amount="-42.50",
        category_id=None,
        status=TransactionStatus.needs_review,
    )

    # Categorized but unmatched.
    unmatched_bank = _make_transaction(
        db_session,
        org_id,
        bank_doc,
        line_number=2,
        txn_date=date(2026, 3, 15),
        description="Client payment",
        amount="1000.00",
        category_id=None,
    )

    # Out of range -- must never appear in the export.
    _make_transaction(
        db_session,
        org_id,
        bank_doc,
        line_number=3,
        txn_date=date(2026, 4, 1),
        description="Later txn",
        amount="-10.00",
    )

    db_session.commit()

    return {
        "headers": headers,
        "matched_expense": matched_expense,
        "matched_bank": matched_bank,
        "unreviewed": unreviewed,
        "unmatched_bank": unmatched_bank,
    }


def test_export_csv_includes_every_transaction_with_status_columns(client, db_session, unique_email):
    ctx = _setup_mixed_transactions(client, db_session, unique_email)

    response = client.get(
        "/export/transactions",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31", "format": "csv"},
        headers=ctx["headers"],
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

    reader = csv.DictReader(io.StringIO(response.text))
    rows = {row["id"]: row for row in reader}

    assert set(rows) == {
        str(ctx["matched_expense"].id),
        str(ctx["matched_bank"].id),
        str(ctx["unreviewed"].id),
        str(ctx["unmatched_bank"].id),
    }

    matched_row = rows[str(ctx["matched_bank"].id)]
    assert matched_row["category"] == "Travel"
    assert matched_row["review_status"] == "resolved"
    assert matched_row["match_status"] == "matched"

    unreviewed_row = rows[str(ctx["unreviewed"].id)]
    assert unreviewed_row["category"] == "Uncategorized"
    assert unreviewed_row["review_status"] == "needs_review"
    assert unreviewed_row["match_status"] == "unmatched"

    unmatched_bank_row = rows[str(ctx["unmatched_bank"].id)]
    assert unmatched_bank_row["category"] == "Uncategorized"
    assert unmatched_bank_row["review_status"] == "resolved"
    assert unmatched_bank_row["match_status"] == "unmatched"


def test_export_json_includes_every_transaction_with_status_columns(client, db_session, unique_email):
    ctx = _setup_mixed_transactions(client, db_session, unique_email)

    response = client.get(
        "/export/transactions",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31", "format": "json"},
        headers=ctx["headers"],
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    rows = {row["id"]: row for row in response.json()}

    assert set(rows) == {
        str(ctx["matched_expense"].id),
        str(ctx["matched_bank"].id),
        str(ctx["unreviewed"].id),
        str(ctx["unmatched_bank"].id),
    }

    matched_row = rows[str(ctx["matched_expense"].id)]
    assert matched_row["category"] == "Travel"
    assert matched_row["review_status"] == "resolved"
    assert matched_row["match_status"] == "matched"

    unreviewed_row = rows[str(ctx["unreviewed"].id)]
    assert unreviewed_row["category"] == "Uncategorized"
    assert unreviewed_row["review_status"] == "needs_review"
    assert unreviewed_row["match_status"] == "unmatched"


def test_export_rejects_inverted_date_range(client, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])

    response = client.get(
        "/export/transactions",
        params={"start_date": "2026-03-31", "end_date": "2026-03-01", "format": "csv"},
        headers=headers,
    )

    assert response.status_code == 422


def test_export_requires_auth(client):
    response = client.get(
        "/export/transactions",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31", "format": "csv"},
    )
    assert response.status_code == 401


def test_export_scoped_to_organization(client, db_session, unique_email):
    owner_a = _signup(client, unique_email, org_name="Org A")
    headers_a = _auth_headers(owner_a["access_token"])

    owner_b = _signup(client, f"other-{unique_email}", org_name="Org B")
    org_b_id = uuid.UUID(owner_b["organization"]["id"])

    bank_doc_b = _make_document(db_session, org_b_id, DocumentType.bank_statement, "statement.csv")
    _make_transaction(
        db_session, org_b_id, bank_doc_b, txn_date=date(2026, 3, 5), description="Org B txn", amount="-40.00"
    )
    db_session.commit()

    response = client.get(
        "/export/transactions",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31", "format": "json"},
        headers=headers_a,
    )

    assert response.status_code == 200
    assert response.json() == []
