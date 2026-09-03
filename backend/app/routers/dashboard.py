import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, get_current_principal
from app.models import Category, Document, DocumentType, ReconciliationMatch, Transaction
from app.schemas import CategorySummaryOut, DashboardFlagsOut, DashboardSummaryOut, TransactionOut
from app.scoping import org_scoped_select

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _validate_range(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "start_date must not be after end_date")


def _bank_transactions_in_range(
    db: Session, org_id: uuid.UUID, start_date: date, end_date: date
) -> list[Transaction]:
    stmt = (
        org_scoped_select(Transaction, org_id)
        .join(Document, Transaction.document_id == Document.id)
        .where(Document.doc_type == DocumentType.bank_statement)
        .where(Transaction.txn_date >= start_date)
        .where(Transaction.txn_date <= end_date)
    )
    return list(db.execute(stmt).scalars())


@router.get("/summary", response_model=DashboardSummaryOut)
def get_summary(
    start_date: date = Query(...),
    end_date: date = Query(...),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DashboardSummaryOut:
    """Income/expense picture grouped by Category for an arbitrary,
    user-selected date range (issue #8, AC1/AC2).

    Cash-basis: only bank-statement Transactions carry actual money
    movement (CONTEXT.md, Transaction -- amount sign carries direction).
    Expense-source (invoice/receipt) Transactions are documentation, not
    confirmed cash movement -- an unmatched one surfaces separately as a
    missing-documentation flag (see get_flags) rather than being summed
    here, which would double-count once it reconciles against its bank
    Transaction.
    """
    _validate_range(start_date, end_date)

    transactions = _bank_transactions_in_range(db, principal.org_id, start_date, end_date)

    category_ids = {t.category_id for t in transactions if t.category_id is not None}
    category_names: dict[uuid.UUID, str] = {}
    if category_ids:
        cat_stmt = org_scoped_select(Category, principal.org_id).where(Category.id.in_(category_ids))
        category_names = {c.id: c.name for c in db.execute(cat_stmt).scalars()}

    buckets: dict[uuid.UUID | None, dict[str, object]] = {}
    for txn in transactions:
        bucket = buckets.setdefault(
            txn.category_id, {"income": Decimal("0"), "expenses": Decimal("0"), "count": 0}
        )
        if txn.amount >= 0:
            bucket["income"] = bucket["income"] + txn.amount  # type: ignore[operator]
        else:
            bucket["expenses"] = bucket["expenses"] + txn.amount  # type: ignore[operator]
        bucket["count"] = bucket["count"] + 1  # type: ignore[operator]

    category_summaries = [
        CategorySummaryOut(
            category_id=category_id,
            category_name=category_names.get(category_id, "Uncategorized")
            if category_id is not None
            else "Uncategorized",
            income=data["income"],  # type: ignore[arg-type]
            expenses=data["expenses"],  # type: ignore[arg-type]
            transaction_count=data["count"],  # type: ignore[arg-type]
        )
        for category_id, data in buckets.items()
    ]
    category_summaries.sort(key=lambda c: c.category_name)

    income_total = sum((c.income for c in category_summaries), Decimal("0"))
    expenses_total = sum((c.expenses for c in category_summaries), Decimal("0"))

    return DashboardSummaryOut(
        start_date=start_date,
        end_date=end_date,
        income_total=income_total,
        expenses_total=expenses_total,
        net_total=income_total + expenses_total,
        categories=category_summaries,
    )


@router.get("/summary/transactions", response_model=list[TransactionOut])
def get_summary_transactions(
    start_date: date = Query(...),
    end_date: date = Query(...),
    category_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[Transaction]:
    """Drill down from one summary line to its underlying bank Transactions
    (issue #8, AC4). Each carries `document_id` so the caller can fetch the
    source Document. Pass no `category_id` for the "Uncategorized" line."""
    _validate_range(start_date, end_date)

    stmt = (
        org_scoped_select(Transaction, principal.org_id)
        .join(Document, Transaction.document_id == Document.id)
        .where(Document.doc_type == DocumentType.bank_statement)
        .where(Transaction.txn_date >= start_date)
        .where(Transaction.txn_date <= end_date)
        .where(Transaction.category_id == category_id)
        .order_by(Transaction.txn_date)
    )
    return list(db.execute(stmt).scalars())


@router.get("/flags", response_model=DashboardFlagsOut)
def get_flags(
    start_date: date = Query(...),
    end_date: date = Query(...),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DashboardFlagsOut:
    """Missing-documentation flags: Transactions on either side of
    reconciliation that never matched, within the date range (issue #8,
    AC3). Each carries `document_id` for drill-down to the source Document
    (AC4)."""
    _validate_range(start_date, end_date)

    matched_ids = db.execute(
        select(ReconciliationMatch.bank_transaction_id, ReconciliationMatch.expense_transaction_id).where(
            ReconciliationMatch.org_id == principal.org_id
        )
    ).all()
    excluded = {t_id for pair in matched_ids for t_id in pair}

    def _unmatched(doc_type: DocumentType) -> list[Transaction]:
        stmt = (
            org_scoped_select(Transaction, principal.org_id)
            .join(Document, Transaction.document_id == Document.id)
            .where(Document.doc_type == doc_type)
            .where(Transaction.txn_date >= start_date)
            .where(Transaction.txn_date <= end_date)
            .order_by(Transaction.txn_date)
        )
        return [t for t in db.execute(stmt).scalars() if t.id not in excluded]

    return DashboardFlagsOut(
        start_date=start_date,
        end_date=end_date,
        unmatched_bank_transactions=[
            TransactionOut.model_validate(t) for t in _unmatched(DocumentType.bank_statement)
        ],
        unmatched_expense_transactions=[
            TransactionOut.model_validate(t) for t in _unmatched(DocumentType.invoice_or_receipt)
        ],
    )
