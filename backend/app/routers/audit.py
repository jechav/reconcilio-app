import uuid
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import OrgRole, Principal, require_role
from app.models import SYSTEM_ACTOR, AuditLogEntry, OrgMembership, User
from app.schemas import AuditLogEntryOut
from app.scoping import org_scoped_select

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


def _actor_emails(db: Session, org_id: uuid.UUID, actors: set[str]) -> dict[str, str]:
    """Resolve the subset of `actors` that are member User ids of this
    Organization to their email. `"system"` and any other non-UUID or
    non-member actor value are skipped rather than raising -- this is a
    display nicety, not a validated foreign key. Scoped through
    OrgMembership (User itself carries no org_id) so a stray actor value
    can never resolve a User outside the calling Organization.
    """
    user_ids: list[uuid.UUID] = []
    for actor in actors:
        if actor == SYSTEM_ACTOR:
            continue
        try:
            user_ids.append(uuid.UUID(actor))
        except ValueError:
            continue
    if not user_ids:
        return {}

    stmt = (
        select(User.id, User.email)
        .join(OrgMembership, OrgMembership.user_id == User.id)
        .where(OrgMembership.org_id == org_id)
        .where(User.id.in_(user_ids))
    )
    return {str(user_id): email for user_id, email in db.execute(stmt).all()}


@router.get("", response_model=list[AuditLogEntryOut])
def list_audit_log(
    entity_type: str | None = Query(None),
    actor: str | None = Query(None),
    action: str | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    principal: Principal = Depends(require_role(OrgRole.owner, OrgRole.admin)),
    db: Session = Depends(get_db),
) -> list[AuditLogEntryOut]:
    """Chronological AuditLogEntry list for the calling Organization,
    owner/admin only (issue #10, AC1). Newest first.

    Filterable by `entity_type`, `actor` (the literal `"system"` or a User
    id), `action`, and an inclusive `[start_date, end_date]` window over
    `created_at` (AC3) -- every combination is optional and stacks with
    the others.
    """
    stmt = org_scoped_select(AuditLogEntry, principal.org_id)
    if entity_type is not None:
        stmt = stmt.where(AuditLogEntry.entity_type == entity_type)
    if actor is not None:
        stmt = stmt.where(AuditLogEntry.actor == actor)
    if action is not None:
        stmt = stmt.where(AuditLogEntry.action == action)
    if start_date is not None:
        stmt = stmt.where(AuditLogEntry.created_at >= datetime.combine(start_date, time.min, tzinfo=timezone.utc))
    if end_date is not None:
        stmt = stmt.where(AuditLogEntry.created_at <= datetime.combine(end_date, time.max, tzinfo=timezone.utc))
    stmt = stmt.order_by(AuditLogEntry.created_at.desc())

    entries = list(db.execute(stmt).scalars())
    emails = _actor_emails(db, principal.org_id, {entry.actor for entry in entries})

    return [
        AuditLogEntryOut(
            id=entry.id,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            actor=entry.actor,
            actor_email=emails.get(entry.actor),
            action=entry.action,
            before=entry.before,
            after=entry.after,
            created_at=entry.created_at,
        )
        for entry in entries
    ]
