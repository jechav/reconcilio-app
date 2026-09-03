"""configurable categorization: categories, category corrections, and
Transaction category assignment

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "name", name="uq_categories_org_name"),
    )
    op.create_index("ix_categories_org_id", "categories", ["org_id"])

    op.add_column(
        "transactions",
        sa.Column(
            "category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id"), nullable=True
        ),
    )
    op.add_column("transactions", sa.Column("category_confidence", sa.Float(), nullable=True))

    op.create_table(
        "category_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id"),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column(
            "category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_category_corrections_org_id", "category_corrections", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_category_corrections_org_id", table_name="category_corrections")
    op.drop_table("category_corrections")

    op.drop_column("transactions", "category_confidence")
    op.drop_column("transactions", "category_id")

    op.drop_index("ix_categories_org_id", table_name="categories")
    op.drop_table("categories")
