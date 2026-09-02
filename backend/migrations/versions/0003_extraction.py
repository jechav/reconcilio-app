"""transactions, extraction results, audit log; org confidence threshold

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

transaction_status = postgresql.ENUM(
    "needs_review", "resolved", name="transaction_status", create_type=False
)
extraction_method = postgresql.ENUM(
    "ocr", "llm", "structured_parse", name="extraction_method", create_type=False
)


def upgrade() -> None:
    # A Document is needs_review whenever any of its Transactions is.
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'needs_review' BEFORE 'done'")

    op.add_column(
        "organizations",
        sa.Column("confidence_threshold", sa.Float(), nullable=False, server_default="0.8"),
    )

    transaction_status.create(op.get_bind(), checkfirst=True)
    extraction_method.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False
        ),
        sa.Column("line_number", sa.BigInteger(), nullable=False),
        sa.Column("txn_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", transaction_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "line_number", name="uq_transactions_document_line"),
    )
    op.create_index("ix_transactions_org_id", "transactions", ["org_id"])
    op.create_index("ix_transactions_document_id", "transactions", ["document_id"])

    op.create_table(
        "extraction_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False
        ),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id"),
            nullable=True,
        ),
        sa.Column("line_number", sa.BigInteger(), nullable=False),
        sa.Column("method", extraction_method, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_extraction_results_org_id", "extraction_results", ["org_id"])
    op.create_index("ix_extraction_results_document_id", "extraction_results", ["document_id"])

    op.create_table(
        "audit_log_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_entries_org_id", "audit_log_entries", ["org_id"])
    op.create_index("ix_audit_log_entries_entity_id", "audit_log_entries", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_entries_entity_id", table_name="audit_log_entries")
    op.drop_index("ix_audit_log_entries_org_id", table_name="audit_log_entries")
    op.drop_table("audit_log_entries")

    op.drop_index("ix_extraction_results_document_id", table_name="extraction_results")
    op.drop_index("ix_extraction_results_org_id", table_name="extraction_results")
    op.drop_table("extraction_results")

    op.drop_index("ix_transactions_document_id", table_name="transactions")
    op.drop_index("ix_transactions_org_id", table_name="transactions")
    op.drop_table("transactions")

    extraction_method.drop(op.get_bind())
    transaction_status.drop(op.get_bind())

    op.drop_column("organizations", "confidence_threshold")
    # Postgres cannot remove a value from an enum type; document_status keeps
    # 'needs_review' after a downgrade, which is harmless (no row uses it).
