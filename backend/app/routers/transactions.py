import uuid

from litestar import Router, get, put
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.params import FromPath, JSONBody
from litestar.status_codes import HTTP_404_NOT_FOUND
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import AuditLogEntry, Category, CategoryCorrection, Transaction
from app.schemas import TransactionCategoryCorrectionRequest, TransactionOut
from app.scoping import org_scoped_select


@get("/{transaction_id:uuid}", sync_to_thread=True)
def get_transaction(
    transaction_id: FromPath[uuid.UUID],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> TransactionOut:
    transaction = (
        db.execute(org_scoped_select(Transaction, principal.org_id).where(Transaction.id == transaction_id))
        .scalars()
        .one_or_none()
    )
    if transaction is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Transaction not found")
    return TransactionOut.model_validate(transaction)


@put("/{transaction_id:uuid}/category", sync_to_thread=True)
def correct_category(
    transaction_id: FromPath[uuid.UUID],
    data: JSONBody[TransactionCategoryCorrectionRequest],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> TransactionOut:
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
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Transaction not found")

    category = (
        db.execute(org_scoped_select(Category, principal.org_id).where(Category.id == data.category_id))
        .scalars()
        .one_or_none()
    )
    if category is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Category not found")

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
    return TransactionOut.model_validate(transaction)


router = Router(
    path="/transactions",
    route_handlers=[get_transaction, correct_category],
    tags=["transactions"],
)
