import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, get_current_principal
from app.models import AuditLogEntry, Category, CategoryCorrection, Transaction
from app.schemas import TransactionCategoryCorrectionRequest, TransactionOut
from app.scoping import org_scoped_select

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(
    transaction_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Transaction:
    transaction = (
        db.execute(org_scoped_select(Transaction, principal.org_id).where(Transaction.id == transaction_id))
        .scalars()
        .one_or_none()
    )
    if transaction is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    return transaction


@router.put("/{transaction_id}/category", response_model=TransactionOut)
def correct_category(
    transaction_id: uuid.UUID,
    payload: TransactionCategoryCorrectionRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Transaction:
    """A user corrects one Transaction's Category (issue #5, AC4). Any org
    member may correct -- CONTEXT.md's OrgMembership role governs actions
    like invites/billing, not this kind of everyday data edit (member can
    already upload/categorize/view).

    The correction is stored as org-scoped few-shot context for *future*
    suggestions (AC5) and never touches any other Transaction's Category
    (AC6) -- it only ever updates the one row matched by `transaction_id`.
    """
    transaction = (
        db.execute(org_scoped_select(Transaction, principal.org_id).where(Transaction.id == transaction_id))
        .scalars()
        .one_or_none()
    )
    if transaction is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")

    category = (
        db.execute(org_scoped_select(Category, principal.org_id).where(Category.id == payload.category_id))
        .scalars()
        .one_or_none()
    )
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")

    before_category_id = transaction.category_id
    transaction.category_id = category.id
    # A human correction is authoritative -- unlike an LLM suggestion, there
    # is no uncertainty left to represent.
    transaction.category_confidence = 1.0

    db.add(
        AuditLogEntry(
            org_id=principal.org_id,
            actor=str(principal.user_id),
            action="transaction.category_corrected",
            entity_type="transaction",
            entity_id=transaction.id,
            before={"category_id": str(before_category_id) if before_category_id else None},
            after={"category_id": str(category.id)},
        )
    )
    db.add(
        CategoryCorrection(
            org_id=principal.org_id,
            transaction_id=transaction.id,
            description=transaction.description,
            category_id=category.id,
        )
    )
    db.commit()
    db.refresh(transaction)
    return transaction
