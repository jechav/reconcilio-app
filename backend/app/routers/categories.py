import uuid

from litestar import Router, delete, get, patch, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.params import FromPath, JSONBody
from litestar.status_codes import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from sqlalchemy.orm import Session

from app.auth import Principal, require_role
from app.models import AuditLogEntry, Category, OrgRole, Transaction
from app.schemas import CategoryCreateRequest, CategoryOut, CategoryUpdateRequest
from app.scoping import org_scoped_select


@get("/", sync_to_thread=True)
def list_categories(
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> list[CategoryOut]:
    stmt = org_scoped_select(Category, principal.org_id).order_by(Category.name)
    return [CategoryOut.model_validate(category) for category in db.execute(stmt).scalars()]


@post("/", sync_to_thread=True, guards=[require_role(OrgRole.owner, OrgRole.admin)])
def create_category(
    data: JSONBody[CategoryCreateRequest],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> CategoryOut:
    """Owner/admin only (issue #5, AC2). Includes the seeded starter set --
    those are ordinary Categories, not protected in any way."""
    existing = (
        db.execute(org_scoped_select(Category, principal.org_id).where(Category.name == data.name))
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail="Category already exists")

    category = Category(org_id=principal.org_id, name=data.name)
    db.add(category)
    db.flush()
    db.add(
        AuditLogEntry(
            org_id=principal.org_id,
            actor=str(principal.user_id),
            action="category.created",
            entity_type="category",
            entity_id=category.id,
            before=None,
            after={"name": category.name},
        )
    )
    db.commit()
    db.refresh(category)
    return CategoryOut.model_validate(category)


@patch("/{category_id:uuid}", sync_to_thread=True, guards=[require_role(OrgRole.owner, OrgRole.admin)])
def update_category(
    category_id: FromPath[uuid.UUID],
    data: JSONBody[CategoryUpdateRequest],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> CategoryOut:
    category = (
        db.execute(org_scoped_select(Category, principal.org_id).where(Category.id == category_id))
        .scalars()
        .one_or_none()
    )
    if category is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Category not found")

    if data.name != category.name:
        clash = (
            db.execute(
                org_scoped_select(Category, principal.org_id).where(Category.name == data.name)
            )
            .scalars()
            .one_or_none()
        )
        if clash is not None:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail="Category already exists")

    before = {"name": category.name}
    category.name = data.name
    db.add(
        AuditLogEntry(
            org_id=principal.org_id,
            actor=str(principal.user_id),
            action="category.updated",
            entity_type="category",
            entity_id=category.id,
            before=before,
            after={"name": category.name},
        )
    )
    db.commit()
    db.refresh(category)
    return CategoryOut.model_validate(category)


@delete("/{category_id:uuid}", sync_to_thread=True, guards=[require_role(OrgRole.owner, OrgRole.admin)])
def delete_category(
    category_id: FromPath[uuid.UUID],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> None:
    category = (
        db.execute(org_scoped_select(Category, principal.org_id).where(Category.id == category_id))
        .scalars()
        .one_or_none()
    )
    if category is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Category not found")

    # Deleting a Category never deletes or reassigns the Transactions it was
    # assigned to -- it just un-sets that one reference, so no other
    # Transaction's Category is retroactively touched (issue #5, AC6 spirit)
    # and past ExtractionResult/AuditLogEntry rows referencing it are
    # untouched too.
    affected = (
        db.execute(org_scoped_select(Transaction, principal.org_id).where(Transaction.category_id == category_id))
        .scalars()
        .all()
    )
    for transaction in affected:
        transaction.category_id = None
        transaction.category_confidence = None

    db.add(
        AuditLogEntry(
            org_id=principal.org_id,
            actor=str(principal.user_id),
            action="category.deleted",
            entity_type="category",
            entity_id=category.id,
            before={"name": category.name},
            after=None,
        )
    )
    db.delete(category)
    db.commit()


router = Router(
    path="/categories",
    route_handlers=[list_categories, create_category, update_category, delete_category],
    tags=["categories"],
)
