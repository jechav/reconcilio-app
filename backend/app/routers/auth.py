from litestar import Router, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.params import JSONBody
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import STARTER_CATEGORY_NAMES, Category, OrgMembership, OrgRole, Organization, User
from app.schemas import (
    AcceptInviteRequest,
    LoginRequest,
    OrganizationOut,
    SignupRequest,
    TokenResponse,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password

def _token_response(user: User, membership: OrgMembership) -> TokenResponse:
    token = create_access_token(user_id=user.id, org_id=membership.org_id, role=membership.role)
    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        organization=OrganizationOut.model_validate(membership.organization),
        role=membership.role,
    )


@post("/signup", sync_to_thread=True)
def signup(data: JSONBody[SignupRequest], db: NamedDependency[Session]) -> TokenResponse:
    existing = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=data.email, hashed_password=hash_password(data.password))
    organization = Organization(name=data.org_name)
    db.add(user)
    db.add(organization)
    db.flush()

    # Every new Organization starts with a flat starter set of Categories
    # (issue #5, AC1) -- ordinary, editable/deletable rows, not special-cased
    # anywhere else in the app.
    for name in STARTER_CATEGORY_NAMES:
        db.add(Category(org_id=organization.id, name=name))

    membership = OrgMembership(user_id=user.id, org_id=organization.id, role=OrgRole.owner)
    db.add(membership)
    db.commit()
    db.refresh(membership)

    return _token_response(user, membership)


@post("/accept-invite", status_code=HTTP_200_OK, sync_to_thread=True)
def accept_invite(data: JSONBody[AcceptInviteRequest], db: NamedDependency[Session]) -> TokenResponse:
    user = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
    if user is None or user.hashed_password is not None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="No pending invite for this email")

    membership = db.execute(
        select(OrgMembership)
        .where(OrgMembership.user_id == user.id)
        .order_by(OrgMembership.created_at)
    ).scalars().first()
    if membership is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="No pending invite for this email")

    user.hashed_password = hash_password(data.password)
    db.commit()
    db.refresh(membership)

    return _token_response(user, membership)


@post("/login", status_code=HTTP_200_OK, sync_to_thread=True)
def login(data: JSONBody[LoginRequest], db: NamedDependency[Session]) -> TokenResponse:
    user = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
    if user is None or user.hashed_password is None or not verify_password(
        data.password, user.hashed_password
    ):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    membership = db.execute(
        select(OrgMembership)
        .where(OrgMembership.user_id == user.id)
        .order_by(OrgMembership.created_at)
    ).scalars().first()
    if membership is None:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="User has no organization")

    return _token_response(user, membership)


router = Router(
    path="/auth",
    route_handlers=[signup, accept_invite, login],
    tags=["auth"],
    opt={"exclude_from_auth": True},
)
