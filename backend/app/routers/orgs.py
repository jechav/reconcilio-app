from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, get_current_principal, require_role
from app.llm_usage import usage_summary
from app.models import OrgMembership, OrgRole, Organization, User
from app.schemas import InviteRequest, LlmUsageOut, MembershipOut, OrganizationOut, OrgSettingsUpdate
from app.scoping import org_scoped_select

router = APIRouter(prefix="/orgs/me", tags=["organizations"])


@router.patch("/settings", response_model=OrganizationOut)
def update_settings(
    payload: OrgSettingsUpdate,
    principal: Principal = Depends(require_role(OrgRole.owner)),
    db: Session = Depends(get_db),
) -> Organization:
    """Owner-only: set the Organization's extraction confidence threshold
    (issue #3, AC4) -- fields below this are sent through llm_refine."""
    organization = db.get(Organization, principal.org_id)
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")

    organization.confidence_threshold = payload.confidence_threshold
    db.commit()
    db.refresh(organization)
    return organization


@router.get("/llm-usage", response_model=list[LlmUsageOut])
def get_llm_usage(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[LlmUsageOut]:
    """Per-tenant LLM usage/cost tracking, queryable (issue #7, AC5):
    total calls per provider/model for the calling Organization."""
    return [
        LlmUsageOut(provider=total.provider, model=total.model, calls=total.calls)
        for total in usage_summary(db, principal.org_id)
    ]


@router.get("/members", response_model=list[MembershipOut])
def list_members(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[OrgMembership]:
    stmt = org_scoped_select(OrgMembership, principal.org_id).order_by(OrgMembership.created_at)
    return list(db.execute(stmt).scalars())


@router.post("/members", response_model=MembershipOut, status_code=status.HTTP_201_CREATED)
def invite_member(
    payload: InviteRequest,
    principal: Principal = Depends(require_role(OrgRole.owner)),
    db: Session = Depends(get_db),
) -> OrgMembership:
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user is None:
        user = User(email=payload.email, hashed_password=None)
        db.add(user)
        db.flush()

    already_member = (
        db.execute(org_scoped_select(OrgMembership, principal.org_id).where(OrgMembership.user_id == user.id))
        .scalars()
        .one_or_none()
    )
    if already_member is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "User is already a member of this organization")

    membership = OrgMembership(user_id=user.id, org_id=principal.org_id, role=payload.role)
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return membership
