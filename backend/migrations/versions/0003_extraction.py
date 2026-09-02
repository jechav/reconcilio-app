"""real extraction: confidence threshold, extraction_results, transactions,
audit_log_entries

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

extraction_method = postgresql.ENUM(
    "ocr", "llm", "structured_parse", name="extraction_method", create_type=False
)
review_status = postgresql.ENUM("ok", "needs_review", name="review_status", create_type=False)


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "confidence_threshold", sa.Numeric(3, 2), nullable=False, server_default="0.80"
        ),
    )

    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'needs_review'")

    extraction_method.create(op.get_bind(), checkfirst=True)
    review_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "extraction_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False
        ),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=1024), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("method", extraction_method, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_extraction_results_org_id", "extraction_results", ["org_id"])
    op.create_index("ix_extraction_results_document_id", "extraction_results", ["document_id"])

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False
        ),
        sa.Column("vendor", sa.String(length=255), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("review_status", review_status, nullable=False, server_default="ok"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_transactions_org_id", "transactions", ["org_id"])
    op.create_index("ix_transactions_document_id", "transactions", ["document_id"])

    op.create_table(
        "audit_log_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("actor", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_entries_org_id", "audit_log_entries", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_entries_org_id", table_name="audit_log_entries")
    op.drop_table("audit_log_entries")

    op.drop_index("ix_transactions_document_id", table_name="transactions")
    op.drop_index("ix_transactions_org_id", table_name="transactions")
    op.drop_table("transactions")

    op.drop_index("ix_extraction_results_document_id", table_name="extraction_results")
    op.drop_index("ix_extraction_results_org_id", table_name="extraction_results")
    op.drop_table("extraction_results")

    review_status.drop(op.get_bind())
    extraction_method.drop(op.get_bind())

    # Postgres cannot drop a single enum value; 'needs_review' stays defined
    # on document_status. Harmless no-op on downgrade.

    op.drop_column("organizations", "confidence_threshold")


