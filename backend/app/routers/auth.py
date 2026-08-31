from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import OrgMembership, OrgRole, Organization, User
from app.schemas import (
    AcceptInviteRequest,
    LoginRequest,
    OrganizationOut,
    SignupRequest,
    TokenResponse,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user: User, membership: OrgMembership) -> TokenResponse:
    token = create_access_token(user_id=user.id, org_id=membership.org_id, role=membership.role)
    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        organization=OrganizationOut.model_validate(membership.organization),
        role=membership.role,
    )


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    existing = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    organization = Organization(name=payload.org_name)
    db.add(user)
    db.add(organization)
    db.flush()

    membership = OrgMembership(user_id=user.id, org_id=organization.id, role=OrgRole.owner)
    db.add(membership)
    db.commit()
    db.refresh(membership)

    return _token_response(user, membership)


@router.post("/accept-invite", response_model=TokenResponse, status_code=status.HTTP_200_OK)
def accept_invite(payload: AcceptInviteRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user is None or user.hashed_password is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending invite for this email")

    membership = db.execute(
        select(OrgMembership)
        .where(OrgMembership.user_id == user.id)
        .order_by(OrgMembership.created_at)
    ).scalars().first()
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending invite for this email")

    user.hashed_password = hash_password(payload.password)
    db.commit()
    db.refresh(membership)

    return _token_response(user, membership)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user is None or user.hashed_password is None or not verify_password(
        payload.password, user.hashed_password
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    membership = db.execute(
        select(OrgMembership)
        .where(OrgMembership.user_id == user.id)
        .order_by(OrgMembership.created_at)
    ).scalars().first()
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User has no organization")

    return _token_response(user, membership)
