"""The HTTP error contract the frontend was written against under FastAPI.

Litestar's defaults differ (validation is 400, error bodies carry
`status_code` and `extra`), so app/errors.py and app/auth.py pin the old
shapes. These tests are the guard for that: they must keep passing through
every router port.
"""


def _signup(client, email):
    response = client.post(
        "/auth/signup",
        json={"email": email, "password": "correct-horse", "org_name": "Acme Tax"},
    )
    assert response.status_code == 201
    return response.json()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_missing_token_is_401_with_detail_only(client):
    response = client.get("/orgs/me/members")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_non_bearer_scheme_is_401_not_authenticated(client):
    response = client.get("/orgs/me/members", headers={"Authorization": "Basic abc"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_invalid_token_is_401_with_detail_only(client):
    response = client.get("/orgs/me/members", headers=_auth_headers("not-a-jwt"))

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired token"}


def test_insufficient_role_is_403_with_detail_only(client, unique_email):
    owner = _signup(client, unique_email)
    invitee_email = f"invitee-{unique_email}"
    client.post(
        "/orgs/me/members",
        json={"email": invitee_email, "role": "member"},
        headers=_auth_headers(owner["access_token"]),
    )
    member = client.post(
        "/auth/accept-invite", json={"email": invitee_email, "password": "correct-horse"}
    ).json()

    response = client.patch(
        "/orgs/me/settings",
        json={"confidence_threshold": "0.5"},
        headers=_auth_headers(member["access_token"]),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient role"}


def test_invalid_body_is_422_with_error_list(client, unique_email):
    owner = _signup(client, unique_email)

    response = client.patch(
        "/orgs/me/settings",
        json={"confidence_threshold": "5"},
        headers=_auth_headers(owner["access_token"]),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail
    assert set(detail[0]) == {"loc", "msg", "type"}
    assert "confidence_threshold" in detail[0]["loc"]


def test_handler_http_error_body_is_detail_only(client, unique_email):
    owner = _signup(client, unique_email)
    headers = _auth_headers(owner["access_token"])
    invite = {"email": f"invitee-{unique_email}", "role": "member"}
    client.post("/orgs/me/members", json=invite, headers=headers)

    response = client.post("/orgs/me/members", json=invite, headers=headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "User is already a member of this organization"}


def test_unknown_route_is_404_without_a_token(client):
    response = client.get("/no-such-route")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_cors_preflight_on_protected_route_needs_no_token(client):
    response = client.options(
        "/orgs/me/members",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code < 300
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
