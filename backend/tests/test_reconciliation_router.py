"""API-boundary tests for manual reconciliation matching (issue #6, AC6)."""

import uuid
from datetime import date
from decimal import Decimal

from app.models import Document, DocumentStatus, DocumentType, Organization, Transaction, TransactionStatus


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


def _make_transaction(db_session, org_id, document, *, line_number=1, txn_date, description, amount):
    transaction = Transaction(
        org_id=org_id,
        document_id=document.id,
        line_number=line_number,
        txn_date=txn_date,
        description=description,
        amount=Decimal(amount),
        confidence=0.95,
        status=TransactionStatus.resolved,
    )
    db_session.add(transaction)
    db_session.flush()
    return transaction


def test_manual_match_create_and_delete_round_trip(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    expense_doc = _make_document(db_session, org_id, DocumentType.invoice_or_receipt, "invoice.pdf")
    expense_txn = _make_transaction(
        db_session, org_id, expense_doc, txn_date=date(2026, 1, 1), description="Vendor Co", amount="500.00"
    )
    bank_doc = _make_document(db_session, org_id, DocumentType.bank_statement, "statement.csv")
    bank_txn = _make_transaction(
        db_session, org_id, bank_doc, txn_date=date(2026, 6, 1), description="SOMETHING ELSE", amount="-1.00"
    )
    db_session.commit()

    create_response = client.post(
        "/reconciliation/matches",
        json={"bank_transaction_id": str(bank_txn.id), "expense_transaction_id": str(expense_txn.id)},
        headers=headers,
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["match_type"] == "manual"
    assert body["confidence"] == 1.0
    assert body["actor"] == str(uuid.UUID(owner["user"]["id"]))

    list_response = client.get("/reconciliation/matches", headers=headers)
    assert len(list_response.json()) == 1

    unmatched_bank = client.get(
        "/reconciliation/transactions/unmatched", params={"side": "bank_statement"}, headers=headers
    )
    assert unmatched_bank.json() == []

    delete_response = client.delete(f"/reconciliation/matches/{body['id']}", headers=headers)
    assert delete_response.status_code == 204

    list_after_delete = client.get("/reconciliation/matches", headers=headers)
    assert list_after_delete.json() == []

    unmatched_bank_after = client.get(
        "/reconciliation/transactions/unmatched", params={"side": "bank_statement"}, headers=headers
    )
    assert len(unmatched_bank_after.json()) == 1


def test_manual_match_rejects_transactions_outside_org(client, db_session, unique_email):
    owner_a = _signup(client, unique_email, org_name="Org A")
    org_a_id = uuid.UUID(owner_a["organization"]["id"])
    headers_a = _auth_headers(owner_a["access_token"])

    owner_b = _signup(client, f"other-{unique_email}", org_name="Org B")
    org_b_id = uuid.UUID(owner_b["organization"]["id"])

    expense_doc = _make_document(db_session, org_a_id, DocumentType.invoice_or_receipt, "invoice.pdf")
    expense_txn = _make_transaction(
        db_session, org_a_id, expense_doc, txn_date=date(2026, 1, 1), description="Vendor Co", amount="500.00"
    )
    bank_doc = _make_document(db_session, org_b_id, DocumentType.bank_statement, "statement.csv")
    bank_txn = _make_transaction(
        db_session, org_b_id, bank_doc, txn_date=date(2026, 1, 1), description="Vendor Co", amount="-500.00"
    )
    db_session.commit()

    response = client.post(
        "/reconciliation/matches",
        json={"bank_transaction_id": str(bank_txn.id), "expense_transaction_id": str(expense_txn.id)},
        headers=headers_a,
    )

    assert response.status_code == 422


def test_reconciliation_endpoints_require_auth(client):
    response = client.get("/reconciliation/matches")
    assert response.status_code == 401
