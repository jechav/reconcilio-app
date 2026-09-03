"""API-boundary tests for Category CRUD and the Transaction correction
endpoint (issue #5, AC2/AC4/AC6/AC7/AC8)."""

import uuid

from app.models import STARTER_CATEGORY_NAMES


def _signup(client, email, org_name="Acme Tax"):
    response = client.post(
        "/auth/signup",
        json={"email": email, "password": "correct-horse", "org_name": org_name},
    )
    assert response.status_code == 201
    return response.json()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _add_member(client, owner_headers, email, role="member"):
    invited = client.post("/orgs/me/members", json={"email": email, "role": role}, headers=owner_headers)
    assert invited.status_code == 201
    accepted = client.post("/auth/accept-invite", json={"email": email, "password": "correct-horse"})
    assert accepted.status_code == 200
    return accepted.json()


def test_new_organization_is_seeded_with_starter_categories(client, unique_email):
    owner = _signup(client, unique_email)

    response = client.get("/categories", headers=_auth_headers(owner["access_token"]))

    assert response.status_code == 200
    names = {c["name"] for c in response.json()}
    assert names == set(STARTER_CATEGORY_NAMES)


def test_owner_can_create_category(client, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])

    response = client.post("/categories", json={"name": "Marketing"}, headers=headers)

    assert response.status_code == 201
    assert response.json()["name"] == "Marketing"


def test_creating_duplicate_category_name_is_rejected(client, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])

    response = client.post("/categories", json={"name": "Travel"}, headers=headers)

    assert response.status_code == 409


def test_member_cannot_create_category(client, unique_email):
    owner = _signup(client, unique_email)
    owner_headers = _auth_headers(owner["access_token"])
    member = _add_member(client, owner_headers, f"member-{unique_email}", role="member")

    response = client.post(
        "/categories", json={"name": "Marketing"}, headers=_auth_headers(member["access_token"])
    )

    assert response.status_code == 403


def test_admin_can_edit_a_seeded_category(client, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])
    categories = client.get("/categories", headers=headers).json()
    travel = next(c for c in categories if c["name"] == "Travel")

    response = client.patch(f"/categories/{travel['id']}", json={"name": "Trips"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["name"] == "Trips"


def test_owner_can_delete_a_seeded_category(client, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])
    categories = client.get("/categories", headers=headers).json()
    other = next(c for c in categories if c["name"] == "Other")

    response = client.delete(f"/categories/{other['id']}", headers=headers)

    assert response.status_code == 204
    remaining = {c["name"] for c in client.get("/categories", headers=headers).json()}
    assert "Other" not in remaining


def test_deleting_a_category_unsets_it_on_assigned_transactions_only(client, db_session, unique_email):
    import datetime
    from decimal import Decimal

    from app.models import Category, Document, DocumentStatus, DocumentType, Transaction, TransactionStatus

    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])
    categories = client.get("/categories", headers=headers).json()
    travel = next(c for c in categories if c["name"] == "Travel")
    meals = next(c for c in categories if c["name"] == "Meals")

    document = Document(
        org_id=org_id,
        filename="statement.csv",
        content_type="text/csv",
        size_bytes=1,
        minio_key=f"{org_id}/{uuid.uuid4()}-statement.csv",
        doc_type=DocumentType.bank_statement,
        status=DocumentStatus.done,
    )
    db_session.add(document)
    db_session.flush()

    txn_travel = Transaction(
        org_id=org_id,
        document_id=document.id,
        line_number=1,
        txn_date=datetime.date(2026, 1, 1),
        description="Flight",
        amount=Decimal("-100.00"),
        confidence=1.0,
        status=TransactionStatus.resolved,
        category_id=uuid.UUID(travel["id"]),
        category_confidence=0.9,
    )
    txn_meals = Transaction(
        org_id=org_id,
        document_id=document.id,
        line_number=2,
        txn_date=datetime.date(2026, 1, 2),
        description="Lunch",
        amount=Decimal("-20.00"),
        confidence=1.0,
        status=TransactionStatus.resolved,
        category_id=uuid.UUID(meals["id"]),
        category_confidence=0.9,
    )
    db_session.add_all([txn_travel, txn_meals])
    db_session.commit()

    response = client.delete(f"/categories/{travel['id']}", headers=headers)
    assert response.status_code == 204

    db_session.expire_all()
    refreshed_travel = db_session.get(Transaction, txn_travel.id)
    refreshed_meals = db_session.get(Transaction, txn_meals.id)
    assert refreshed_travel.category_id is None
    assert refreshed_travel.category_confidence is None
    # A correction/deletion never retroactively touches any other
    # Transaction's Category (AC6).
    assert refreshed_meals.category_id == uuid.UUID(meals["id"])


def test_categories_are_scoped_to_requesting_org(client, unique_email):
    org_a_owner = _signup(client, unique_email, org_name="Org A")
    org_b_owner = _signup(client, f"other-{unique_email}", org_name="Org B")
    client.post("/categories", json={"name": "OnlyInA"}, headers=_auth_headers(org_a_owner["access_token"]))

    response_b = client.get("/categories", headers=_auth_headers(org_b_owner["access_token"]))

    names_b = {c["name"] for c in response_b.json()}
    assert "OnlyInA" not in names_b


def test_categories_endpoint_requires_auth(client):
    response = client.get("/categories")

    assert response.status_code == 401


def test_user_can_correct_a_transaction_category(client, db_session, unique_email):
    import datetime
    from decimal import Decimal

    from app.models import AuditLogEntry, CategoryCorrection, Document, DocumentStatus, DocumentType, Transaction, TransactionStatus

    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])
    categories = client.get("/categories", headers=headers).json()
    travel = next(c for c in categories if c["name"] == "Travel")
    meals = next(c for c in categories if c["name"] == "Meals")

    document = Document(
        org_id=org_id,
        filename="statement.csv",
        content_type="text/csv",
        size_bytes=1,
        minio_key=f"{org_id}/{uuid.uuid4()}-statement.csv",
        doc_type=DocumentType.bank_statement,
        status=DocumentStatus.done,
    )
    db_session.add(document)
    db_session.flush()
    transaction = Transaction(
        org_id=org_id,
        document_id=document.id,
        line_number=1,
        txn_date=datetime.date(2026, 1, 1),
        description="Airline Co",
        amount=Decimal("-250.00"),
        confidence=1.0,
        status=TransactionStatus.resolved,
        category_id=uuid.UUID(meals["id"]),
        category_confidence=0.55,
    )
    db_session.add(transaction)
    db_session.commit()

    response = client.put(
        f"/transactions/{transaction.id}/category",
        json={"category_id": travel["id"]},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["category_id"] == travel["id"]
    assert body["category_confidence"] == 1.0

    audit_entries = db_session.query(AuditLogEntry).filter_by(
        org_id=org_id, entity_id=transaction.id, action="transaction.category_corrected"
    ).all()
    assert len(audit_entries) == 1
    entry = audit_entries[0]
    assert entry.actor == str(uuid.UUID(owner["user"]["id"]))
    assert entry.before == {"category_id": meals["id"]}
    assert entry.after == {"category_id": travel["id"]}

    corrections = db_session.query(CategoryCorrection).filter_by(org_id=org_id).all()
    assert len(corrections) == 1
    assert corrections[0].description == "Airline Co"
    assert str(corrections[0].category_id) == travel["id"]


def test_correcting_one_transaction_never_touches_another(client, db_session, unique_email):
    import datetime
    from decimal import Decimal

    from app.models import Document, DocumentStatus, DocumentType, Transaction, TransactionStatus

    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])
    categories = client.get("/categories", headers=headers).json()
    travel = next(c for c in categories if c["name"] == "Travel")
    meals = next(c for c in categories if c["name"] == "Meals")

    document = Document(
        org_id=org_id,
        filename="statement.csv",
        content_type="text/csv",
        size_bytes=1,
        minio_key=f"{org_id}/{uuid.uuid4()}-statement.csv",
        doc_type=DocumentType.bank_statement,
        status=DocumentStatus.done,
    )
    db_session.add(document)
    db_session.flush()
    txn_a = Transaction(
        org_id=org_id, document_id=document.id, line_number=1,
        txn_date=datetime.date(2026, 1, 1), description="Airline Co", amount=Decimal("-250.00"),
        confidence=1.0, status=TransactionStatus.resolved,
        category_id=uuid.UUID(meals["id"]), category_confidence=0.55,
    )
    txn_b = Transaction(
        org_id=org_id, document_id=document.id, line_number=2,
        txn_date=datetime.date(2026, 1, 2), description="Cafe", amount=Decimal("-15.00"),
        confidence=1.0, status=TransactionStatus.resolved,
        category_id=uuid.UUID(meals["id"]), category_confidence=0.9,
    )
    db_session.add_all([txn_a, txn_b])
    db_session.commit()

    response = client.put(
        f"/transactions/{txn_a.id}/category", json={"category_id": travel["id"]}, headers=headers
    )
    assert response.status_code == 200

    db_session.expire_all()
    refreshed_b = db_session.get(Transaction, txn_b.id)
    assert refreshed_b.category_id == uuid.UUID(meals["id"])
    assert refreshed_b.category_confidence == 0.9


def test_correction_requires_auth(client):
    response = client.put(f"/transactions/{uuid.uuid4()}/category", json={"category_id": str(uuid.uuid4())})

    assert response.status_code == 401


def test_correction_rejects_unknown_category(client, db_session, unique_email):
    import datetime
    from decimal import Decimal

    from app.models import Document, DocumentStatus, DocumentType, Transaction, TransactionStatus

    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    document = Document(
        org_id=org_id,
        filename="statement.csv",
        content_type="text/csv",
        size_bytes=1,
        minio_key=f"{org_id}/{uuid.uuid4()}-statement.csv",
        doc_type=DocumentType.bank_statement,
        status=DocumentStatus.done,
    )
    db_session.add(document)
    db_session.flush()
    transaction = Transaction(
        org_id=org_id, document_id=document.id, line_number=1,
        txn_date=datetime.date(2026, 1, 1), description="Airline Co", amount=Decimal("-250.00"),
        confidence=1.0, status=TransactionStatus.resolved,
    )
    db_session.add(transaction)
    db_session.commit()

    response = client.put(
        f"/transactions/{transaction.id}/category",
        json={"category_id": str(uuid.uuid4())},
        headers=headers,
    )

    assert response.status_code == 404
