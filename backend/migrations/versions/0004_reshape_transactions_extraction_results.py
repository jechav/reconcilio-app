"""reshape transactions/extraction_results to the consolidated 0003 shape

0003_extraction.py's file content was rewritten in place (same revision id)
during the issue #3/#4 merge-conflict resolution to unify the two branches'
independently-shaped `transactions`/`extraction_results` tables. Alembic
tracks applied migrations by revision id, not file content, so any database
that had already run the *original* 0003 (the issue #3-only shape: `vendor`,
`transaction_date`, `review_status` on transactions; `field_name`/`value` on
extraction_results) is stamped "0003" and never re-applies the rewritten
file -- it silently keeps the stale schema while the app code (and any
freshly-created database) expects the new one. This migration is the
forward-compatible fix: it reshapes an already-migrated database from the
old 0003 shape to the current one, preserving existing rows rather than
requiring a reset. A database that only ever saw the rewritten 0003 (i.e.
already has `line_number`/`description`/`status`/`fields`) has nothing for
this migration to do -- every step below is a no-op guarded by
`checkfirst`/`IF EXISTS`.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

transaction_status = postgresql.ENUM(
    "needs_review", "resolved", name="transaction_status", create_type=False
)


def _has_column(bind: sa.engine.Connection, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # --- transactions: vendor/transaction_date/review_status -> ---------
    # --- line_number/description/txn_date/status -------------------------
    if _has_column(bind, "transactions", "review_status"):
        transaction_status.create(bind, checkfirst=True)

        op.add_column("transactions", sa.Column("line_number", sa.BigInteger(), nullable=True))
        op.execute("UPDATE transactions SET line_number = 1 WHERE line_number IS NULL")
        op.alter_column("transactions", "line_number", nullable=False)

        op.add_column("transactions", sa.Column("description", sa.String(length=512), nullable=True))
        op.execute("UPDATE transactions SET description = COALESCE(vendor, '') WHERE description IS NULL")
        op.alter_column("transactions", "description", nullable=False)
        op.drop_column("transactions", "vendor")

        op.alter_column("transactions", "transaction_date", new_column_name="txn_date")
        op.execute("UPDATE transactions SET txn_date = created_at::date WHERE txn_date IS NULL")
        op.alter_column("transactions", "txn_date", nullable=False)

        op.execute("UPDATE transactions SET amount = 0 WHERE amount IS NULL")
        op.alter_column(
            "transactions", "amount", type_=sa.Numeric(precision=14, scale=2), nullable=False
        )

        op.execute("UPDATE transactions SET confidence = 0 WHERE confidence IS NULL")
        op.alter_column("transactions", "confidence", type_=sa.Float(), nullable=False)

        op.add_column(
            "transactions", sa.Column("status", transaction_status, nullable=True)
        )
        op.execute(
            "UPDATE transactions SET status = CASE WHEN review_status = 'needs_review' "
            "THEN 'needs_review'::transaction_status ELSE 'resolved'::transaction_status END"
        )
        op.alter_column("transactions", "status", nullable=False)
        op.drop_column("transactions", "review_status")
        op.execute("DROP TYPE IF EXISTS review_status")

        op.create_unique_constraint(
            "uq_transactions_document_line", "transactions", ["document_id", "line_number"]
        )

    # --- extraction_results: field_name/value -> fields (jsonb) ----------
    if _has_column(bind, "extraction_results", "field_name"):
        op.alter_column("extraction_results", "confidence", type_=sa.Float())

        op.add_column(
            "extraction_results",
            sa.Column(
                "transaction_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("transactions.id"),
                nullable=True,
            ),
        )
        op.add_column("extraction_results", sa.Column("line_number", sa.BigInteger(), nullable=True))
        op.execute("UPDATE extraction_results SET line_number = 1 WHERE line_number IS NULL")
        op.alter_column("extraction_results", "line_number", nullable=False)

        op.add_column("extraction_results", sa.Column("fields", postgresql.JSONB(), nullable=True))
        op.execute(
            """
            UPDATE extraction_results
            SET fields = jsonb_build_object(
                field_name, jsonb_build_object('value', value, 'confidence', confidence, 'method', method::text)
            )
            WHERE fields IS NULL
            """
        )
        op.alter_column("extraction_results", "fields", nullable=False)
        op.drop_column("extraction_results", "field_name")
        op.drop_column("extraction_results", "value")


def downgrade() -> None:
    # Best-effort reverse; the field_name/value collapse back out of `fields`
    # loses nothing (each row's `fields` dict has exactly one key going into
    # this migration's upgrade path), but a row written post-upgrade with
    # multiple keys in `fields` cannot round-trip -- not expected in
    # practice since this migration only exists to correct migration drift.
    bind = op.get_bind()

    if _has_column(bind, "extraction_results", "fields"):
        op.add_column("extraction_results", sa.Column("field_name", sa.String(length=64), nullable=True))
        op.add_column("extraction_results", sa.Column("value", sa.String(length=1024), nullable=True))
        op.execute(
            """
            UPDATE extraction_results
            SET field_name = (SELECT key FROM jsonb_object_keys(fields) AS key LIMIT 1),
                value = (fields -> (SELECT key FROM jsonb_object_keys(fields) AS key LIMIT 1) ->> 'value')
            """
        )
        op.drop_column("extraction_results", "fields")
        op.drop_column("extraction_results", "line_number")
        op.drop_column("extraction_results", "transaction_id")
        op.alter_column("extraction_results", "confidence", type_=sa.Numeric(precision=4, scale=3))

    if _has_column(bind, "transactions", "status"):
        review_status = postgresql.ENUM(
            "ok", "needs_review", name="review_status", create_type=False
        )
        review_status.create(bind, checkfirst=True)
        op.add_column(
            "transactions",
            sa.Column("review_status", review_status, nullable=True),
        )
        op.execute(
            "UPDATE transactions SET review_status = CASE WHEN status = 'needs_review' "
            "THEN 'needs_review'::review_status ELSE 'ok'::review_status END"
        )
        op.alter_column("transactions", "review_status", nullable=False, server_default="ok")
        op.drop_constraint("uq_transactions_document_line", "transactions", type_="unique")
        op.drop_column("transactions", "status")
        transaction_status.drop(bind, checkfirst=True)

        op.alter_column("transactions", "confidence", type_=sa.Numeric(precision=4, scale=3))

        op.add_column("transactions", sa.Column("vendor", sa.String(length=255), nullable=True))
        op.execute("UPDATE transactions SET vendor = description")
        op.drop_column("transactions", "description")

        op.alter_column("transactions", "txn_date", new_column_name="transaction_date", nullable=True)
        op.drop_column("transactions", "line_number")
