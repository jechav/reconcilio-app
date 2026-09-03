"""Reconciliation matching -- auto (pipeline-triggered) and manual (API).

`run_reconciliation_for_document(document_id, db)` is the auto-match entry
point: called whenever a Document finishes extraction (see
app/pipeline.py:run_pipeline), it walks that Document's freshly-persisted
Transactions and, for each one still unmatched, looks for a one-to-one
ReconciliationMatch among the *opposite* side's unmatched Transactions
within a rolling +/-60-day candidate window (not tied to a fixed calendar
period -- see CONTEXT.md, ReconciliationMatch).

A match requires an exact-or-near-exact amount (+/-$1) and a date within
+/-5 days; vendor-name similarity is computed and recorded for visibility
but never filters candidates (ADR/CONTEXT: "supporting, non-filtering
signal"). When more than one candidate clears both filters, the closest
date wins and the resulting match is flagged lower-confidence rather than
guessed at combinatorially (ADR-0002: strictly one-to-one, no split/combined
auto-matches).

`create_manual_match` / `remove_manual_match` are the API-boundary
functions behind POST/DELETE /reconciliation/matches: a human can link two
Transactions the algorithm missed, or unlink one it got wrong, without
having to satisfy the algorithmic criteria -- only the one-to-one
constraint still applies (CONTEXT.md, Manual match).

Both paths write an AuditLogEntry recording the acting actor ("system" for
automatic matches, the user's id for manual ones), same convention as the
extraction pipeline.
"""

from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    SYSTEM_ACTOR,
    AuditLogEntry,
    Document,
    DocumentType,
    MatchType,
    ReconciliationMatch,
    Transaction,
)

#: Candidate pool bound -- broader than the actual match criteria below, so
#: it never itself excludes a legitimate match; it just keeps the query scoped.
CANDIDATE_WINDOW_DAYS = 60
AMOUNT_TOLERANCE = Decimal("1.00")
DATE_TOLERANCE_DAYS = 5

#: A single qualifying candidate is a clean match; more than one means the
#: algorithm had to break a tie by closest date, which is flagged lower.
CONFIDENCE_CLEAN = 0.95
CONFIDENCE_TIE_BROKEN = 0.6
CONFIDENCE_MANUAL = 1.0


class ReconciliationError(RuntimeError):
    """Raised for a manual-match request that violates the one-to-one rule
    or the API boundary's own preconditions (not the algorithm's criteria,
    which manual matches are exempt from)."""


def _side(doc_type: DocumentType) -> str:
    return "bank" if doc_type == DocumentType.bank_statement else "expense"


def _opposite_doc_type(doc_type: DocumentType) -> DocumentType:
    return (
        DocumentType.invoice_or_receipt
        if doc_type == DocumentType.bank_statement
        else DocumentType.bank_statement
    )


def vendor_similarity(a: str, b: str) -> float:
    """0-1 similarity between two descriptions -- a supporting signal only,
    never used to filter or rank candidates (see module docstring)."""
    return difflib.SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def _is_matched(db: Session, transaction_id: uuid.UUID) -> bool:
    existing = db.execute(
        select(ReconciliationMatch.id).where(
            (ReconciliationMatch.bank_transaction_id == transaction_id)
            | (ReconciliationMatch.expense_transaction_id == transaction_id)
        )
    ).scalar_one_or_none()
    return existing is not None


def _matched_transaction_ids(db: Session, org_id: uuid.UUID) -> set[uuid.UUID]:
    rows = db.execute(
        select(ReconciliationMatch.bank_transaction_id, ReconciliationMatch.expense_transaction_id).where(
            ReconciliationMatch.org_id == org_id
        )
    ).all()
    matched: set[uuid.UUID] = set()
    for bank_id, expense_id in rows:
        matched.add(bank_id)
        matched.add(expense_id)
    return matched


@dataclass
class _Candidate:
    transaction: Transaction
    date_diff_days: int


def _candidates_for(db: Session, transaction: Transaction, org_id: uuid.UUID, doc_type: DocumentType) -> list[_Candidate]:
    window_start = transaction.txn_date - timedelta(days=CANDIDATE_WINDOW_DAYS)
    window_end = transaction.txn_date + timedelta(days=CANDIDATE_WINDOW_DAYS)

    pool = (
        db.execute(
            select(Transaction)
            .join(Document, Transaction.document_id == Document.id)
            .where(
                Transaction.org_id == org_id,
                Document.doc_type == _opposite_doc_type(doc_type),
                Transaction.txn_date >= window_start,
                Transaction.txn_date <= window_end,
            )
        )
        .scalars()
        .all()
    )

    matched_ids = _matched_transaction_ids(db, org_id)

    eligible: list[_Candidate] = []
    for candidate in pool:
        if candidate.id in matched_ids:
            continue
        amount_diff = abs(abs(candidate.amount) - abs(transaction.amount))
        if amount_diff > AMOUNT_TOLERANCE:
            continue
        date_diff = abs((candidate.txn_date - transaction.txn_date).days)
        if date_diff > DATE_TOLERANCE_DAYS:
            continue
        eligible.append(_Candidate(transaction=candidate, date_diff_days=date_diff))

    return eligible


def _record_match(
    db: Session,
    *,
    org_id: uuid.UUID,
    bank_txn: Transaction,
    expense_txn: Transaction,
    match_type: MatchType,
    confidence: float,
    actor: str,
    extra_audit_fields: dict[str, object] | None = None,
) -> ReconciliationMatch:
    match = ReconciliationMatch(
        org_id=org_id,
        bank_transaction_id=bank_txn.id,
        expense_transaction_id=expense_txn.id,
        match_type=match_type,
        confidence=confidence,
        actor=actor,
    )
    db.add(match)
    db.flush()

    after: dict[str, object] = {
        "bank_transaction_id": str(bank_txn.id),
        "expense_transaction_id": str(expense_txn.id),
        "match_type": match_type.value,
        "confidence": confidence,
    }
    if extra_audit_fields:
        after.update(extra_audit_fields)

    db.add(
        AuditLogEntry(
            org_id=org_id,
            actor=actor,
            action="reconciliation_match.created",
            entity_type="reconciliation_match",
            entity_id=match.id,
            before=None,
            after=after,
        )
    )
    return match


def _match_one(db: Session, transaction: Transaction, org_id: uuid.UUID, doc_type: DocumentType) -> ReconciliationMatch | None:
    if _is_matched(db, transaction.id):
        return None

    eligible = _candidates_for(db, transaction, org_id, doc_type)
    if not eligible:
        return None

    eligible.sort(key=lambda c: c.date_diff_days)
    is_tied = len(eligible) > 1
    best = eligible[0].transaction
    confidence = CONFIDENCE_TIE_BROKEN if is_tied else CONFIDENCE_CLEAN

    side = _side(doc_type)
    bank_txn, expense_txn = (transaction, best) if side == "bank" else (best, transaction)

    match = _record_match(
        db,
        org_id=org_id,
        bank_txn=bank_txn,
        expense_txn=expense_txn,
        match_type=MatchType.automatic,
        confidence=confidence,
        actor=SYSTEM_ACTOR,
        extra_audit_fields={
            "tied_candidates": len(eligible),
            "vendor_similarity": vendor_similarity(transaction.description, best.description),
        },
    )
    db.commit()
    db.refresh(match)
    return match


def run_reconciliation_for_document(document_id: uuid.UUID, db: Session) -> list[ReconciliationMatch]:
    """Auto-match every Transaction of a just-extracted Document.

    Called by the pipeline once a Document's Transactions are persisted
    (issue #6, AC1). Safe to call on a Document with zero Transactions (an
    unknown-classification or fully-rejected extraction) -- it's just a
    no-op then.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise ReconciliationError(f"Document {document_id} not found")

    transactions = (
        db.execute(select(Transaction).where(Transaction.document_id == document_id).order_by(Transaction.line_number))
        .scalars()
        .all()
    )

    created: list[ReconciliationMatch] = []
    for transaction in transactions:
        match = _match_one(db, transaction, document.org_id, document.doc_type)
        if match is not None:
            created.append(match)
    return created


def create_manual_match(
    db: Session,
    *,
    org_id: uuid.UUID,
    bank_transaction_id: uuid.UUID,
    expense_transaction_id: uuid.UUID,
    actor: str,
) -> ReconciliationMatch:
    """Link two Transactions by hand -- exempt from the algorithm's
    amount/date/vendor criteria, but not from the one-to-one rule."""
    bank_txn = db.get(Transaction, bank_transaction_id)
    expense_txn = db.get(Transaction, expense_transaction_id)

    if bank_txn is None or bank_txn.org_id != org_id:
        raise ReconciliationError("bank_transaction_id not found")
    if expense_txn is None or expense_txn.org_id != org_id:
        raise ReconciliationError("expense_transaction_id not found")

    bank_document = db.get(Document, bank_txn.document_id)
    expense_document = db.get(Document, expense_txn.document_id)
    if bank_document is None or bank_document.doc_type != DocumentType.bank_statement:
        raise ReconciliationError("bank_transaction_id must belong to a bank_statement Document")
    if expense_document is None or expense_document.doc_type != DocumentType.invoice_or_receipt:
        raise ReconciliationError("expense_transaction_id must belong to an invoice_or_receipt Document")

    if _is_matched(db, bank_txn.id):
        raise ReconciliationError("bank transaction is already matched")
    if _is_matched(db, expense_txn.id):
        raise ReconciliationError("expense transaction is already matched")

    match = _record_match(
        db,
        org_id=org_id,
        bank_txn=bank_txn,
        expense_txn=expense_txn,
        match_type=MatchType.manual,
        confidence=CONFIDENCE_MANUAL,
        actor=actor,
    )
    db.commit()
    db.refresh(match)
    return match


def remove_manual_match(db: Session, *, org_id: uuid.UUID, match_id: uuid.UUID, actor: str) -> None:
    """Unlink a ReconciliationMatch a human decided was wrong -- automatic
    or manual, either can be removed this way (CONTEXT.md, Manual match)."""
    match = db.get(ReconciliationMatch, match_id)
    if match is None or match.org_id != org_id:
        raise ReconciliationError("ReconciliationMatch not found")

    before = {
        "bank_transaction_id": str(match.bank_transaction_id),
        "expense_transaction_id": str(match.expense_transaction_id),
        "match_type": match.match_type.value,
        "confidence": match.confidence,
    }

    db.add(
        AuditLogEntry(
            org_id=org_id,
            actor=actor,
            action="reconciliation_match.removed",
            entity_type="reconciliation_match",
            entity_id=match.id,
            before=before,
            after=None,
        )
    )
    db.delete(match)
    db.commit()
