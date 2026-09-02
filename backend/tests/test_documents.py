import httpx

from app.extraction.textract import ExtractedField, TextractExpenseResult


def _signup(client, email, org_name="Acme Tax"):
    response = client.post(
        "/auth/signup",
        json={"email": email, "password": "correct-horse", "org_name": org_name},
    )
    assert response.status_code == 201
    return response.json()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _request_upload(client, headers, **overrides):
    payload = {
        "filename": "invoice.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1024,
        "doc_type": "invoice_or_receipt",
    }
    payload.update(overrides)
    return client.post("/documents", json=payload, headers=headers)


def test_request_upload_creates_queued_document_and_presigned_url(client, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])

    response = _request_upload(client, headers)

    assert response.status_code == 201
    body = response.json()
    assert body["document"]["status"] == "queued"
    assert body["document"]["filename"] == "invoice.pdf"
    assert body["upload_url"].startswith("http")


def test_request_upload_rejects_oversized_file_before_queueing(client, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])

    response = _request_upload(client, headers, size_bytes=21 * 1024 * 1024)

    assert response.status_code == 422
    assert "exceeds maximum size" in response.json()["detail"]


def test_request_upload_rejects_unsupported_extension_before_queueing(client, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])

    response = _request_upload(client, headers, filename="malware.exe", content_type="application/octet-stream")

    assert response.status_code == 422
    assert "Unsupported file type" in response.json()["detail"]


class _FakeTextractClient:
    """Stands in for AWS Textract in HTTP-level tests -- no live credentials
    are available in this environment (issue #3)."""

    def detect_text(self, document_bytes: bytes) -> list[str]:
        return ["INVOICE", "Vendor Co", "TOTAL", "Bill To"]

    def analyze_expense(self, document_bytes: bytes) -> TextractExpenseResult:
        return TextractExpenseResult(
            fields=[
                ExtractedField(name="vendor", value="Vendor Co", confidence=0.95),
                ExtractedField(name="amount", value="123.45", confidence=0.95),
                ExtractedField(name="invoice_date", value="2026-01-01", confidence=0.95),
            ]
        )


def _upload_and_complete(client, headers, monkeypatch, document_id, upload_url):
    monkeypatch.setattr("app.pipeline.get_textract_client", lambda: _FakeTextractClient())
    httpx.put(upload_url, content=b"%PDF-1.4 fake invoice bytes for testing").raise_for_status()
    return client.post(f"/documents/{document_id}/complete", headers=headers)


def test_complete_upload_queues_pipeline_and_document_reaches_done(client, unique_email, monkeypatch):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])
    upload = _request_upload(client, headers).json()

    response = _upload_and_complete(
        client, headers, monkeypatch, upload["document"]["id"], upload["upload_url"]
    )

    assert response.status_code == 200
    # Celery runs eagerly in tests, so the pipeline has already finished.
    assert response.json()["status"] == "done"

    status_response = client.get(f"/documents/{upload['document']['id']}", headers=headers)
    assert status_response.json()["status"] == "done"


def test_complete_upload_rejects_already_completed_document(client, unique_email, monkeypatch):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])
    upload = _request_upload(client, headers).json()
    _upload_and_complete(client, headers, monkeypatch, upload["document"]["id"], upload["upload_url"])

    response = client.post(f"/documents/{upload['document']['id']}/complete", headers=headers)

    assert response.status_code == 409


def test_document_status_is_scoped_to_owning_org(client, unique_email):
    owner_a = _signup(client, unique_email, org_name="Org A")
    document_id = _request_upload(client, _auth_headers(owner_a["access_token"])).json()["document"]["id"]

    other_email = f"other-{unique_email}"
    owner_b = _signup(client, other_email, org_name="Org B")

    response = client.get(f"/documents/{document_id}", headers=_auth_headers(owner_b["access_token"]))

    assert response.status_code == 404


def test_documents_endpoints_require_auth(client):
    response = client.get("/documents/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 401
