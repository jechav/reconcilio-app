import uuid
from typing import Any, Protocol, TypeVar

from sqlalchemy import Select, select


class _HasOrgId(Protocol):
    org_id: Any


OrgScopedModel = TypeVar("OrgScopedModel", bound=_HasOrgId)


def org_scoped_select(model: type[OrgScopedModel], org_id: uuid.UUID) -> Select[tuple[OrgScopedModel]]:
    """Build a SELECT for an Organization-scoped model, always filtered by org_id.

    Every query against a model that carries an org_id column must go through
    this helper rather than filtering ad hoc in route handlers, so Organization
    isolation lives at the ORM layer and can't be forgotten in a new endpoint.
    """
    return select(model).where(model.org_id == org_id)
