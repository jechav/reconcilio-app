import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, get_current_principal, require_role
from app.models import AuditLogEntry, Category, OrgRole, Transaction
from app.schemas import CategoryCreateRequest, CategoryOut, CategoryUpdateRequest
from app.scoping import org_scoped_select

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
def list_categories(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[Category]:
    stmt = org_scoped_select(Category, principal.org_id).order_by(Category.name)
    return list(db.execute(stmt).scalars())


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreateRequest,
    principal: Principal = Depends(require_role(OrgRole.owner, OrgRole.admin)),
    db: Session = Depends(get_db),
) -> Category:
    """Owner/admin only (issue #5, AC2). Includes the seeded starter set --
    those are ordinary Categories, not protected in any way."""
    existing = (
        db.execute(org_scoped_select(Category, principal.org_id).where(Category.name == payload.name))
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Category already exists")

    category = Category(org_id=principal.org_id, name=payload.name)
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
    return category


@router.patch("/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdateRequest,
    principal: Principal = Depends(require_role(OrgRole.owner, OrgRole.admin)),
    db: Session = Depends(get_db),
) -> Category:
    category = (
        db.execute(org_scoped_select(Category, principal.org_id).where(Category.id == category_id))
        .scalars()
        .one_or_none()
    )
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")

    if payload.name != category.name:
        clash = (
            db.execute(
                org_scoped_select(Category, principal.org_id).where(Category.name == payload.name)
            )
            .scalars()
            .one_or_none()
        )
        if clash is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Category already exists")

    before = {"name": category.name}
    category.name = payload.name
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
    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: uuid.UUID,
    principal: Principal = Depends(require_role(OrgRole.owner, OrgRole.admin)),
    db: Session = Depends(get_db),
) -> Response:
    category = (
        db.execute(org_scoped_select(Category, principal.org_id).where(Category.id == category_id))
        .scalars()
        .one_or_none()
    )
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")

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
    return Response(status_code=status.HTTP_204_NO_CONTENT)
