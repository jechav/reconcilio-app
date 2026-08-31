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
