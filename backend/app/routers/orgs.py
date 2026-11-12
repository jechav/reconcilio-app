from litestar import Router, get, patch, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.params import JSONBody
from litestar.status_codes import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, require_role
from app.llm_usage import usage_summary
from app.models import OrgMembership, OrgRole, Organization, User
from app.schemas import InviteRequest, LlmUsageOut, MembershipOut, OrganizationOut, OrgSettingsUpdate
from app.scoping import org_scoped_select


@patch("/settings", sync_to_thread=True, guards=[require_role(OrgRole.owner)])
def update_settings(
    data: JSONBody[OrgSettingsUpdate],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> OrganizationOut:
    """Owner-only: set the Organization's extraction confidence threshold
    (issue #3, AC4) -- fields below this are sent through llm_refine."""
    organization = db.get(Organization, principal.org_id)
    if organization is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Organization not found")

    organization.confidence_threshold = data.confidence_threshold
    db.commit()
    db.refresh(organization)
    return OrganizationOut.model_validate(organization)


@get("/llm-usage", sync_to_thread=True)
def get_llm_usage(
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> list[LlmUsageOut]:
    """Per-tenant LLM usage/cost tracking, queryable (issue #7, AC5):
    total calls per provider/model for the calling Organization."""
    return [
        LlmUsageOut(provider=total.provider, model=total.model, calls=total.calls)
        for total in usage_summary(db, principal.org_id)
    ]


@get("/members", sync_to_thread=True)
def list_members(
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> list[MembershipOut]:
    stmt = org_scoped_select(OrgMembership, principal.org_id).order_by(OrgMembership.created_at)
    return [MembershipOut.model_validate(membership) for membership in db.execute(stmt).scalars()]


@post("/members", sync_to_thread=True, guards=[require_role(OrgRole.owner)])
def invite_member(
    data: JSONBody[InviteRequest],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> MembershipOut:
    user = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
    if user is None:
        user = User(email=data.email, hashed_password=None)
        db.add(user)
        db.flush()

    already_member = (
        db.execute(org_scoped_select(OrgMembership, principal.org_id).where(OrgMembership.user_id == user.id))
        .scalars()
        .one_or_none()
    )
    if already_member is not None:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT, detail="User is already a member of this organization"
        )

    membership = OrgMembership(user_id=user.id, org_id=principal.org_id, role=data.role)
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return MembershipOut.model_validate(membership)


router = Router(
    path="/orgs/me",
    route_handlers=[update_settings, get_llm_usage, list_members, invite_member],
    tags=["organizations"],
)
