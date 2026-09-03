"""documents table

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

document_type = postgresql.ENUM(
    "invoice_or_receipt", "bank_statement", name="document_type", create_type=False
)
document_status = postgresql.ENUM(
    "queued", "processing", "done", "failed", name="document_status", create_type=False
)


def upgrade() -> None:
    document_type.create(op.get_bind(), checkfirst=True)
    document_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("minio_key", sa.String(length=512), nullable=False),
        sa.Column("doc_type", document_type, nullable=False),
        sa.Column("status", document_status, nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("minio_key", name="uq_documents_minio_key"),
    )
    op.create_index("ix_documents_org_id", "documents", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_org_id", table_name="documents")
    op.drop_table("documents")
    document_status.drop(op.get_bind())
    document_type.drop(op.get_bind())
