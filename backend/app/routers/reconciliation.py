import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, get_current_principal
from app.models import Document, DocumentType, ReconciliationMatch, Transaction
from app.reconciliation import ReconciliationError, create_manual_match, remove_manual_match
from app.schemas import ManualMatchRequest, ReconciliationMatchOut, TransactionOut
from app.scoping import org_scoped_select

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.get("/matches", response_model=list[ReconciliationMatchOut])
def list_matches(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[ReconciliationMatch]:
    stmt = org_scoped_select(ReconciliationMatch, principal.org_id).order_by(
        ReconciliationMatch.created_at.desc()
    )
    return list(db.execute(stmt).scalars())


@router.post("/matches", response_model=ReconciliationMatchOut, status_code=status.HTTP_201_CREATED)
def create_match(
    payload: ManualMatchRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ReconciliationMatch:
    """A user manually links two Transactions the algorithm missed
    (issue #6, AC6). Not subject to the algorithm's amount/date/vendor
    criteria -- only the one-to-one rule still applies."""
    try:
        return create_manual_match(
            db,
            org_id=principal.org_id,
            bank_transaction_id=payload.bank_transaction_id,
            expense_transaction_id=payload.expense_transaction_id,
            actor=str(principal.user_id),
        )
    except ReconciliationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.delete("/matches/{match_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_match(
    match_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    """A user removes a ReconciliationMatch the algorithm (or another user)
    got wrong -- automatic or manual matches can both be unmatched this way."""
    try:
        remove_manual_match(db, org_id=principal.org_id, match_id=match_id, actor=str(principal.user_id))
    except ReconciliationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/transactions/unmatched", response_model=list[TransactionOut])
def list_unmatched_transactions(
    side: DocumentType = Query(..., description="bank_statement or invoice_or_receipt"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[Transaction]:
    """Unmatched Transactions on either side are the tax-risk signal
    (CONTEXT.md, Expense-source Transaction) -- both sides are queryable
    here (issue #6, AC5)."""
    matched_ids = db.execute(
        select(ReconciliationMatch.bank_transaction_id, ReconciliationMatch.expense_transaction_id).where(
            ReconciliationMatch.org_id == principal.org_id
        )
    ).all()
    excluded = {t_id for pair in matched_ids for t_id in pair}

    stmt = (
        org_scoped_select(Transaction, principal.org_id)
        .join(Document, Transaction.document_id == Document.id)
        .where(Document.doc_type == side)
        .order_by(Transaction.txn_date)
    )
    transactions = list(db.execute(stmt).scalars())
    return [t for t in transactions if t.id not in excluded]
