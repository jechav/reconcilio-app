def _signup(client, email, org_name="Acme Tax"):
    response = client.post(
        "/auth/signup",
        json={"email": email, "password": "correct-horse", "org_name": org_name},
    )
    assert response.status_code == 201
    return response.json()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_owner_can_invite_member_with_role(client, unique_email):
    owner = _signup(client, unique_email)
    invitee_email = f"invitee-{unique_email}"

    response = client.post(
        "/orgs/me/members",
        json={"email": invitee_email, "role": "member"},
        headers=_auth_headers(owner["access_token"]),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "member"
    assert body["user"]["email"] == invitee_email


def test_invite_rejects_duplicate_membership(client, unique_email):
    owner = _signup(client, unique_email)
    invitee_email = f"invitee-{unique_email}"
    headers = _auth_headers(owner["access_token"])
    client.post("/orgs/me/members", json={"email": invitee_email, "role": "member"}, headers=headers)

    response = client.post("/orgs/me/members", json={"email": invitee_email, "role": "admin"}, headers=headers)

    assert response.status_code == 409


def test_non_owner_cannot_invite(client, unique_email):
    owner = _signup(client, unique_email)
    invitee_email = f"invitee-{unique_email}"
    owner_headers = _auth_headers(owner["access_token"])
    client.post("/orgs/me/members", json={"email": invitee_email, "role": "member"}, headers=owner_headers)
    accepted = client.post(
        "/auth/accept-invite", json={"email": invitee_email, "password": "correct-horse"}
    )
    member_headers = _auth_headers(accepted.json()["access_token"])

    response = client.post(
        "/orgs/me/members",
        json={"email": f"another-{unique_email}", "role": "member"},
        headers=member_headers,
    )

    assert response.status_code == 403


def test_members_list_is_scoped_to_requesting_org(client, unique_email):
    org_a_owner = _signup(client, unique_email, org_name="Org A")
    other_email = f"other-{unique_email}"
    org_b_owner = _signup(client, other_email, org_name="Org B")

    response_a = client.get("/orgs/me/members", headers=_auth_headers(org_a_owner["access_token"]))
    response_b = client.get("/orgs/me/members", headers=_auth_headers(org_b_owner["access_token"]))

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    emails_a = {m["user"]["email"] for m in response_a.json()}
    emails_b = {m["user"]["email"] for m in response_b.json()}
    assert emails_a == {unique_email}
    assert emails_b == {other_email}
    assert emails_a.isdisjoint(emails_b)


def test_members_endpoint_requires_auth(client):
    response = client.get("/orgs/me/members")

    assert response.status_code == 401


def test_owner_can_configure_confidence_threshold(client, unique_email):
    owner = _signup(client, unique_email)
    assert owner["organization"]["confidence_threshold"] == "0.80"

    response = client.patch(
        "/orgs/me/settings",
        json={"confidence_threshold": "0.90"},
        headers=_auth_headers(owner["access_token"]),
    )

    assert response.status_code == 200
    assert response.json()["confidence_threshold"] == "0.90"


def test_non_owner_cannot_configure_confidence_threshold(client, unique_email):
    owner = _signup(client, unique_email)
    invitee_email = f"invitee-{unique_email}"
    owner_headers = _auth_headers(owner["access_token"])
    client.post("/orgs/me/members", json={"email": invitee_email, "role": "admin"}, headers=owner_headers)
    accepted = client.post(
        "/auth/accept-invite", json={"email": invitee_email, "password": "correct-horse"}
    )
    admin_headers = _auth_headers(accepted.json()["access_token"])

    response = client.patch(
        "/orgs/me/settings", json={"confidence_threshold": "0.90"}, headers=admin_headers
    )

    assert response.status_code == 403


def test_confidence_threshold_is_actually_used_by_the_pipeline(client, db_session, unique_email, monkeypatch):
    import uuid
    from decimal import Decimal

    from app.extraction.textract import ExtractedField, TextractExpenseResult
    from app.extraction.llm import NullRefiner
    from app.models import Document, DocumentStatus, DocumentType, Organization, Transaction, TransactionStatus
    from app.pipeline import PipelineDeps, run_pipeline

    owner = _signup(client, unique_email)
    org_id = uuid.UUID(owner["organization"]["id"])

    # Lower the threshold so a 0.60-confidence field counts as good enough.
    client.patch(
        "/orgs/me/settings",
        json={"confidence_threshold": "0.50"},
        headers=_auth_headers(owner["access_token"]),
    )
    db_session.expire_all()
    org = db_session.get(Organization, org_id)
    assert org is not None
    assert org.confidence_threshold == Decimal("0.50")

    document = Document(
        org_id=org_id,
        filename="invoice.pdf",
        content_type="application/pdf",
        size_bytes=10,
        minio_key=f"{org_id}/{uuid.uuid4()}-invoice.pdf",
        doc_type=DocumentType.invoice_or_receipt,
        status=DocumentStatus.queued,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    class _Textract:
        def detect_text(self, document_bytes):
            return ["INVOICE", "TOTAL", "Vendor Co"]

        def analyze_expense(self, document_bytes):
            return TextractExpenseResult(
                fields=[
                    ExtractedField(name="vendor", value="Vendor Co", confidence=0.60),
                    ExtractedField(name="amount", value="10.00", confidence=0.95),
                    ExtractedField(name="invoice_date", value="2026-01-01", confidence=0.95),
                ]
            )

        def analyze_document(self, document_bytes):  # pragma: no cover
            raise AssertionError("invoice/receipt path never calls analyze_document")

    class _LLM:
        def refine_field(self, field_name, document_bytes, content_type, current_value):
            raise AssertionError("llm_refine should not run: 0.60 clears the 0.50 threshold")

    deps = PipelineDeps(
        fetch_bytes=lambda key: b"%PDF-1.4 fake",
        textract=_Textract(),
        refiner=NullRefiner(),
        llm_client=_LLM(),
    )
    result = run_pipeline(document.id, db_session, deps)

    assert result.status == DocumentStatus.done
    transaction = db_session.query(Transaction).filter_by(document_id=document.id).one()
    assert transaction.status == TransactionStatus.resolved


def test_llm_usage_endpoint_reports_per_tenant_totals(client, db_session, unique_email):
    """issue #7, AC5: per-tenant LLM usage is tracked and queryable."""
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])

    from app.llm_usage import record_llm_call
    from app.models import Organization

    org = db_session.query(Organization).filter_by(name="Acme Tax").one()
    record_llm_call(db_session, org_id=org.id, document_id=None, provider="openrouter", model="haiku")
    record_llm_call(db_session, org_id=org.id, document_id=None, provider="openrouter", model="haiku")
    db_session.commit()

    response = client.get("/orgs/me/llm-usage", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body == [{"provider": "openrouter", "model": "haiku", "calls": 2}]


def test_llm_usage_endpoint_requires_auth(client):
    response = client.get("/orgs/me/llm-usage")
    assert response.status_code == 401
