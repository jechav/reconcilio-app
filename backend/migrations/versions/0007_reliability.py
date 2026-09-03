"""pipeline reliability & observability

Adds `llm_usage` (per-tenant LLM call tracking, issue #7 AC5) and
`dead_letter_tasks` (Celery tasks that exhausted retries for the same
Document, issue #7 AC2).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=True
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_usage_org_id", "llm_usage", ["org_id"])

    op.create_table(
        "dead_letter_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=True
        ),
        sa.Column(
            "document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False
        ),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("error", sa.String(length=2000), nullable=False),
        sa.Column("attempts", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dead_letter_tasks_org_id", "dead_letter_tasks", ["org_id"])
    op.create_index("ix_dead_letter_tasks_document_id", "dead_letter_tasks", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_dead_letter_tasks_document_id", table_name="dead_letter_tasks")
    op.drop_index("ix_dead_letter_tasks_org_id", table_name="dead_letter_tasks")
    op.drop_table("dead_letter_tasks")
    op.drop_index("ix_llm_usage_org_id", table_name="llm_usage")
    op.drop_table("llm_usage")
