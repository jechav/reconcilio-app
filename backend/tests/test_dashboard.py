"""API-boundary tests for the dashboard summary and flags endpoints
(issue #8, AC1-AC5)."""

import uuid
from datetime import date
from decimal import Decimal

from app.models import (
    Category,
    Document,
    DocumentStatus,
    DocumentType,
    ReconciliationMatch,
    MatchType,
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
    db_session, org_id, document, *, line_number=1, txn_date, description, amount, category_id=None
):
    transaction = Transaction(
        org_id=org_id,
        document_id=document.id,
        line_number=line_number,
        txn_date=txn_date,
        description=description,
        amount=Decimal(amount),
        confidence=0.95,
        status=TransactionStatus.resolved,
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


def test_summary_groups_bank_transactions_by_category_within_range(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])
    travel_id = uuid.UUID(_get_category_id(client, headers, "Travel"))

    bank_doc = _make_document(db_session, org_id, DocumentType.bank_statement, "statement.csv")
    in_range_expense = _make_transaction(
        db_session,
        org_id,
        bank_doc,
        line_number=1,
        txn_date=date(2026, 3, 5),
        description="Airline Co",
        amount="-200.00",
        category_id=travel_id,
    )
    in_range_income = _make_transaction(
        db_session,
        org_id,
        bank_doc,
        line_number=2,
        txn_date=date(2026, 3, 10),
        description="Client payment",
        amount="1000.00",
        category_id=None,
    )
    out_of_range = _make_transaction(
        db_session,
        org_id,
        bank_doc,
        line_number=3,
        txn_date=date(2026, 4, 1),
        description="Later txn",
        amount="-50.00",
        category_id=travel_id,
    )
    db_session.commit()
    assert in_range_expense.id and in_range_income.id and out_of_range.id

    response = client.get(
        "/dashboard/summary",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["income_total"] == "1000.00"
    assert body["expenses_total"] == "-200.00"
    assert body["net_total"] == "800.00"

    by_name = {c["category_name"]: c for c in body["categories"]}
    assert by_name["Travel"]["expenses"] == "-200.00"
    assert by_name["Travel"]["transaction_count"] == 1
    assert by_name["Uncategorized"]["income"] == "1000.00"


def test_summary_excludes_expense_source_transactions(client, db_session, unique_email):
    """Invoice/receipt Transactions are documentation, not confirmed cash
    movement -- they must never appear in the cash-basis summary."""
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    invoice_doc = _make_document(db_session, org_id, DocumentType.invoice_or_receipt, "invoice.pdf")
    _make_transaction(
        db_session,
        org_id,
        invoice_doc,
        txn_date=date(2026, 3, 5),
        description="Vendor Co",
        amount="-300.00",
    )
    db_session.commit()

    response = client.get(
        "/dashboard/summary",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["categories"] == []
    assert body["income_total"] == "0"
    assert body["expenses_total"] == "0"


def test_summary_rejects_inverted_date_range(client, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])

    response = client.get(
        "/dashboard/summary",
        params={"start_date": "2026-03-31", "end_date": "2026-03-01"},
        headers=headers,
    )

    assert response.status_code == 422


def test_summary_drill_down_returns_underlying_transactions(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])
    travel_id = uuid.UUID(_get_category_id(client, headers, "Travel"))

    bank_doc = _make_document(db_session, org_id, DocumentType.bank_statement, "statement.csv")
    txn = _make_transaction(
        db_session,
        org_id,
        bank_doc,
        txn_date=date(2026, 3, 5),
        description="Airline Co",
        amount="-200.00",
        category_id=travel_id,
    )
    db_session.commit()

    response = client.get(
        "/dashboard/summary/transactions",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31", "category_id": str(travel_id)},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(txn.id)
    assert body[0]["document_id"] == str(bank_doc.id)


def test_flags_surface_unmatched_transactions_on_both_sides(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    expense_doc = _make_document(db_session, org_id, DocumentType.invoice_or_receipt, "invoice.pdf")
    matched_expense = _make_transaction(
        db_session, org_id, expense_doc, txn_date=date(2026, 3, 2), description="Vendor Co", amount="-500.00"
    )
    unmatched_expense = _make_transaction(
        db_session,
        org_id,
        expense_doc,
        line_number=2,
        txn_date=date(2026, 3, 3),
        description="Unpaid Vendor",
        amount="-75.00",
    )

    bank_doc = _make_document(db_session, org_id, DocumentType.bank_statement, "statement.csv")
    matched_bank = _make_transaction(
        db_session, org_id, bank_doc, txn_date=date(2026, 3, 4), description="Vendor Co", amount="-500.00"
    )
    unmatched_bank = _make_transaction(
        db_session,
        org_id,
        bank_doc,
        line_number=2,
        txn_date=date(2026, 3, 6),
        description="Mystery charge",
        amount="-20.00",
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
    db_session.commit()

    response = client.get(
        "/dashboard/flags",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    bank_ids = {t["id"] for t in body["unmatched_bank_transactions"]}
    expense_ids = {t["id"] for t in body["unmatched_expense_transactions"]}
    assert bank_ids == {str(unmatched_bank.id)}
    assert expense_ids == {str(unmatched_expense.id)}
    # Each flag carries document_id for drill-down to the source Document (AC4).
    assert body["unmatched_bank_transactions"][0]["document_id"] == str(bank_doc.id)


def test_flags_filter_by_date_range(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    bank_doc = _make_document(db_session, org_id, DocumentType.bank_statement, "statement.csv")
    _make_transaction(
        db_session, org_id, bank_doc, txn_date=date(2026, 1, 1), description="Old charge", amount="-20.00"
    )
    db_session.commit()

    response = client.get(
        "/dashboard/flags",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["unmatched_bank_transactions"] == []


def test_dashboard_endpoints_require_auth(client):
    response = client.get(
        "/dashboard/summary", params={"start_date": "2026-01-01", "end_date": "2026-01-31"}
    )
    assert response.status_code == 401

    response = client.get("/dashboard/flags", params={"start_date": "2026-01-01", "end_date": "2026-01-31"})
    assert response.status_code == 401


def test_dashboard_scoped_to_organization(client, db_session, unique_email):
    owner_a = _signup(client, unique_email, org_name="Org A")
    org_a_id = uuid.UUID(owner_a["organization"]["id"])
    headers_a = _auth_headers(owner_a["access_token"])

    owner_b = _signup(client, f"other-{unique_email}", org_name="Org B")
    org_b_id = uuid.UUID(owner_b["organization"]["id"])

    bank_doc_b = _make_document(db_session, org_b_id, DocumentType.bank_statement, "statement.csv")
    _make_transaction(
        db_session, org_b_id, bank_doc_b, txn_date=date(2026, 3, 5), description="Org B txn", amount="-40.00"
    )
    db_session.commit()
    assert org_a_id != org_b_id

    response = client.get(
        "/dashboard/summary",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
        headers=headers_a,
    )
    assert response.json()["categories"] == []
