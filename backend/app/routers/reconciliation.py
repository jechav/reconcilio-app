import uuid
from typing import Annotated

from litestar import Router, delete, get, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.params import FromPath, JSONBody, QueryParameter
from litestar.status_codes import HTTP_404_NOT_FOUND, HTTP_422_UNPROCESSABLE_ENTITY
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import Document, DocumentType, ReconciliationMatch, Transaction
from app.reconciliation import ReconciliationError, create_manual_match, remove_manual_match
from app.schemas import ManualMatchRequest, ReconciliationMatchOut, TransactionOut
from app.scoping import org_scoped_select


@get("/matches", sync_to_thread=True)
def list_matches(
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> list[ReconciliationMatchOut]:
    stmt = org_scoped_select(ReconciliationMatch, principal.org_id).order_by(
        ReconciliationMatch.created_at.desc()
    )
    return [ReconciliationMatchOut.model_validate(match) for match in db.execute(stmt).scalars()]


@post("/matches", sync_to_thread=True)
def create_match(
    data: JSONBody[ManualMatchRequest],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> ReconciliationMatchOut:
    """A user manually links two Transactions the algorithm missed
    (issue #6, AC6). Not subject to the algorithm's amount/date/vendor
    criteria -- only the one-to-one rule still applies."""
    try:
        match = create_manual_match(
            db,
            org_id=principal.org_id,
            bank_transaction_id=data.bank_transaction_id,
            expense_transaction_id=data.expense_transaction_id,
            actor=str(principal.user_id),
        )
    except ReconciliationError as exc:
        raise HTTPException(status_code=HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return ReconciliationMatchOut.model_validate(match)


@delete("/matches/{match_id:uuid}", sync_to_thread=True)
def delete_match(
    match_id: FromPath[uuid.UUID],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> None:
    """A user removes a ReconciliationMatch the algorithm (or another user)
    got wrong -- automatic or manual matches can both be unmatched this way."""
    try:
        remove_manual_match(db, org_id=principal.org_id, match_id=match_id, actor=str(principal.user_id))
    except ReconciliationError as exc:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@get("/transactions/unmatched", sync_to_thread=True)
def list_unmatched_transactions(
    side: Annotated[DocumentType, QueryParameter(description="bank_statement or invoice_or_receipt")],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> list[TransactionOut]:
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
    return [TransactionOut.model_validate(t) for t in transactions if t.id not in excluded]


router = Router(
    path="/reconciliation",
    route_handlers=[list_matches, create_match, delete_match, list_unmatched_transactions],
    tags=["reconciliation"],
)
