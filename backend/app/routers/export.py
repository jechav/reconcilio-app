import csv
import io
import uuid
from datetime import date
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, get_current_principal
from app.models import Category, ReconciliationMatch, Transaction
from app.schemas import TransactionExportRow
from app.scoping import org_scoped_select

router = APIRouter(prefix="/export", tags=["export"])

_CSV_FIELDS = [
    "id",
    "document_id",
    "txn_date",
    "description",
    "amount",
    "category",
    "review_status",
    "match_status",
]


class ExportFormat(str, Enum):
    csv = "csv"
    json = "json"


def _rows_for_range(db: Session, org_id: uuid.UUID, start_date: date, end_date: date) -> list[TransactionExportRow]:
    """Every Transaction in the Organization within [start_date, end_date],
    regardless of processing state (issue #9, AC1/AC3) -- unlike the
    dashboard (issue #8), this intentionally does not filter to bank-only
    or exclude unreviewed/uncategorized rows, since the export's whole
    point is to surface the items that still need attention alongside the
    clean ones."""
    stmt = (
        org_scoped_select(Transaction, org_id)
        .where(Transaction.txn_date >= start_date)
        .where(Transaction.txn_date <= end_date)
        .order_by(Transaction.txn_date, Transaction.id)
    )
    transactions = list(db.execute(stmt).scalars())

    category_ids = {t.category_id for t in transactions if t.category_id is not None}
    category_names: dict[uuid.UUID, str] = {}
    if category_ids:
        cat_stmt = org_scoped_select(Category, org_id).where(Category.id.in_(category_ids))
        category_names = {c.id: c.name for c in db.execute(cat_stmt).scalars()}

    matched_ids: set[uuid.UUID] = set()
    if transactions:
        match_stmt = select(
            ReconciliationMatch.bank_transaction_id, ReconciliationMatch.expense_transaction_id
        ).where(ReconciliationMatch.org_id == org_id)
        for bank_id, expense_id in db.execute(match_stmt).all():
            matched_ids.add(bank_id)
            matched_ids.add(expense_id)

    return [
        TransactionExportRow(
            id=t.id,
            document_id=t.document_id,
            txn_date=t.txn_date,
            description=t.description,
            amount=t.amount,
            category=category_names.get(t.category_id, "Uncategorized") if t.category_id else "Uncategorized",
            review_status=t.status,
            match_status="matched" if t.id in matched_ids else "unmatched",
        )
        for t in transactions
    ]


def _render_csv(rows: list[TransactionExportRow]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "id": str(row.id),
                "document_id": str(row.document_id),
                "txn_date": row.txn_date.isoformat(),
                "description": row.description,
                "amount": str(row.amount),
                "category": row.category,
                "review_status": row.review_status.value,
                "match_status": row.match_status,
            }
        )
    return buffer.getvalue()


@router.get("/transactions")
def export_transactions(
    start_date: date = Query(...),
    end_date: date = Query(...),
    format: ExportFormat = Query(...),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Response:
    """Export every Transaction in a date range as CSV or JSON, for an
    accountant or tax software (issue #9). Includes Transactions regardless
    of review/match state, with explicit `category`, `review_status`, and
    `match_status` columns so items that still need attention are visible
    rather than silently omitted (AC1-AC3)."""
    if start_date > end_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "start_date must not be after end_date")

    rows = _rows_for_range(db, principal.org_id, start_date, end_date)
    filename = f"transactions_{start_date.isoformat()}_{end_date.isoformat()}.{format.value}"

    if format is ExportFormat.csv:
        content = _render_csv(rows)
        media_type = "text/csv"
    else:
        content = "[" + ",".join(row.model_dump_json() for row in rows) + "]"
        media_type = "application/json"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
