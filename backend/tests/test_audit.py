"""API-boundary tests for the audit trail viewer (issue #10, AC1/AC2/AC3/AC5)."""

import uuid

from app.models import SYSTEM_ACTOR, AuditLogEntry


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


def _add_entry(db_session, org_id, *, actor=SYSTEM_ACTOR, action, entity_type="transaction", **kwargs):
    entry = AuditLogEntry(
        org_id=org_id,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=uuid.uuid4(),
        **kwargs,
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)
    return entry


def test_owner_can_list_the_org_audit_log_chronologically(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    first = _add_entry(db_session, org_id, action="document.extracted", entity_type="document")
    second = _add_entry(db_session, org_id, action="transaction.extracted")

    response = client.get("/audit-log", headers=headers)

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    # Newest first.
    assert ids == [str(second.id), str(first.id)]


def test_entry_shows_entity_actor_action_before_after_and_timestamp(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])
    user_id = str(uuid.UUID(owner["user"]["id"]))

    entry = _add_entry(
        db_session,
        org_id,
        actor=user_id,
        action="transaction.category_corrected",
        entity_type="transaction",
        before={"category_id": None},
        after={"category_id": "cat-1"},
    )

    response = client.get("/audit-log", headers=headers)

    assert response.status_code == 200
    row = next(r for r in response.json() if r["id"] == str(entry.id))
    assert row["entity_type"] == "transaction"
    assert row["entity_id"] == str(entry.entity_id)
    assert row["actor"] == user_id
    assert row["actor_email"] == owner["user"]["email"]
    assert row["action"] == "transaction.category_corrected"
    assert row["before"] == {"category_id": None}
    assert row["after"] == {"category_id": "cat-1"}
    assert row["created_at"]


def test_system_actor_has_no_actor_email(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    _add_entry(db_session, org_id, action="document.extracted", entity_type="document")

    response = client.get("/audit-log", headers=headers)

    row = response.json()[0]
    assert row["actor"] == SYSTEM_ACTOR
    assert row["actor_email"] is None


def test_member_cannot_list_the_audit_log(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    owner_headers = _auth_headers(owner["access_token"])
    member = _add_member(client, owner_headers, f"member-{unique_email}", role="member")

    response = client.get("/audit-log", headers=_auth_headers(member["access_token"]))

    assert response.status_code == 403


def test_admin_can_list_the_audit_log(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    owner_headers = _auth_headers(owner["access_token"])
    admin = _add_member(client, owner_headers, f"admin-{unique_email}", role="admin")

    response = client.get("/audit-log", headers=_auth_headers(admin["access_token"]))

    assert response.status_code == 200


def test_audit_log_is_scoped_to_the_calling_organization(client, db_session, unique_email):
    owner_a = _signup(client, unique_email, org_name="Org A")
    owner_b = _signup(client, f"b-{unique_email}", org_name="Org B")
    org_a_id = uuid.UUID(owner_a["organization"]["id"])
    org_b_id = uuid.UUID(owner_b["organization"]["id"])

    _add_entry(db_session, org_a_id, action="document.extracted", entity_type="document")
    _add_entry(db_session, org_b_id, action="document.extracted", entity_type="document")

    response = client.get("/audit-log", headers=_auth_headers(owner_a["access_token"]))

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_filter_by_entity_type(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    _add_entry(db_session, org_id, action="document.extracted", entity_type="document")
    txn_entry = _add_entry(db_session, org_id, action="transaction.extracted", entity_type="transaction")

    response = client.get("/audit-log", params={"entity_type": "transaction"}, headers=headers)

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert ids == [str(txn_entry.id)]


def test_filter_by_actor(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])
    user_id = str(uuid.UUID(owner["user"]["id"]))

    _add_entry(db_session, org_id, action="document.extracted", entity_type="document")
    manual_entry = _add_entry(
        db_session, org_id, actor=user_id, action="reconciliation_match.created", entity_type="reconciliation_match"
    )

    response = client.get("/audit-log", params={"actor": user_id}, headers=headers)

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert ids == [str(manual_entry.id)]


def test_filter_by_action(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    _add_entry(db_session, org_id, action="reconciliation_match.created", entity_type="reconciliation_match")
    removed_entry = _add_entry(
        db_session, org_id, action="reconciliation_match.removed", entity_type="reconciliation_match"
    )

    response = client.get("/audit-log", params={"action": "reconciliation_match.removed"}, headers=headers)

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert ids == [str(removed_entry.id)]


def test_filter_by_date_range(client, db_session, unique_email):
    from datetime import datetime, timedelta, timezone

    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    old_entry = _add_entry(db_session, org_id, action="document.extracted", entity_type="document")
    old_entry.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    db_session.commit()

    recent_entry = _add_entry(db_session, org_id, action="document.extracted", entity_type="document")

    today = datetime.now(timezone.utc).date()
    response = client.get(
        "/audit-log",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
        headers=headers,
    )

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert ids == [str(recent_entry.id)]
    assert str(old_entry.id) not in ids


def test_filters_can_be_combined(client, db_session, unique_email):
    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])
    headers = _auth_headers(owner["access_token"])

    target = _add_entry(
        db_session,
        org_id,
        actor=SYSTEM_ACTOR,
        action="transaction.category_suggested",
        entity_type="transaction",
    )
    _add_entry(db_session, org_id, action="transaction.extracted", entity_type="transaction")

    response = client.get(
        "/audit-log",
        params={"entity_type": "transaction", "action": "transaction.category_suggested", "actor": SYSTEM_ACTOR},
        headers=headers,
    )

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert ids == [str(target.id)]
