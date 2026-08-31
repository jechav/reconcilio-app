from app.models import OrgMembership, OrgRole, Organization, User


def test_signup_creates_org_and_owner_membership(client, db_session, unique_email):
    response = client.post(
        "/auth/signup",
        json={"email": unique_email, "password": "correct-horse", "org_name": "Acme Tax"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "owner"
    assert body["organization"]["name"] == "Acme Tax"
    assert body["user"]["email"] == unique_email
    assert body["access_token"]

    user = db_session.query(User).filter(User.email == unique_email).one()
    org = db_session.query(Organization).filter(Organization.id == body["organization"]["id"]).one()
    membership = (
        db_session.query(OrgMembership)
        .filter(OrgMembership.user_id == user.id, OrgMembership.org_id == org.id)
        .one()
    )
    assert membership.role == OrgRole.owner


def test_signup_rejects_duplicate_email(client, unique_email):
    payload = {"email": unique_email, "password": "correct-horse", "org_name": "Acme Tax"}
    client.post("/auth/signup", json=payload)

    response = client.post("/auth/signup", json=payload)

    assert response.status_code == 409


def test_login_returns_jwt_session(client, unique_email):
    client.post(
        "/auth/signup",
        json={"email": unique_email, "password": "correct-horse", "org_name": "Acme Tax"},
    )

    response = client.post("/auth/login", json={"email": unique_email, "password": "correct-horse"})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["role"] == "owner"


def test_login_rejects_wrong_password(client, unique_email):
    client.post(
        "/auth/signup",
        json={"email": unique_email, "password": "correct-horse", "org_name": "Acme Tax"},
    )

    response = client.post("/auth/login", json={"email": unique_email, "password": "wrong"})

    assert response.status_code == 401


def test_login_rejects_unknown_email(client, unique_email):
    response = client.post("/auth/login", json={"email": unique_email, "password": "whatever"})

    assert response.status_code == 401


def test_invited_user_can_accept_invite_and_log_in(client, unique_email):
    owner = client.post(
        "/auth/signup",
        json={"email": unique_email, "password": "correct-horse", "org_name": "Acme Tax"},
    ).json()
    invitee_email = f"invitee-{unique_email}"
    client.post(
        "/orgs/me/members",
        json={"email": invitee_email, "role": "member"},
        headers={"Authorization": f"Bearer {owner['access_token']}"},
    )

    accept_response = client.post(
        "/auth/accept-invite", json={"email": invitee_email, "password": "correct-horse"}
    )

    assert accept_response.status_code == 200
    body = accept_response.json()
    assert body["role"] == "member"
    assert body["organization"]["name"] == "Acme Tax"

    login_response = client.post(
        "/auth/login", json={"email": invitee_email, "password": "correct-horse"}
    )
    assert login_response.status_code == 200


def test_accept_invite_rejects_email_with_no_pending_invite(client, unique_email):
    response = client.post(
        "/auth/accept-invite", json={"email": unique_email, "password": "correct-horse"}
    )

    assert response.status_code == 404


def test_accept_invite_rejects_already_active_account(client, unique_email):
    client.post(
        "/auth/signup",
        json={"email": unique_email, "password": "correct-horse", "org_name": "Acme Tax"},
    )

    response = client.post(
        "/auth/accept-invite", json={"email": unique_email, "password": "another-password"}
    )

    assert response.status_code == 404
