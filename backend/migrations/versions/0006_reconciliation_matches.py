"""reconciliation matches

Adds `reconciliation_matches` -- a strictly one-to-one link between a bank
Transaction and an expense-source (invoice/receipt) Transaction, created
either automatically by the matching algorithm or manually by a user (issue
#6, ADR-0002). Each side is unique across the table, which is what makes
one-to-one a database-level guarantee.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

match_type = postgresql.ENUM("automatic", "manual", name="match_type", create_type=False)


def upgrade() -> None:
    match_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "reconciliation_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "bank_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id"),
            nullable=False,
        ),
        sa.Column(
            "expense_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id"),
            nullable=False,
        ),
        sa.Column("match_type", match_type, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bank_transaction_id", name="uq_reconciliation_matches_bank_txn"),
        sa.UniqueConstraint("expense_transaction_id", name="uq_reconciliation_matches_expense_txn"),
    )
    op.create_index("ix_reconciliation_matches_org_id", "reconciliation_matches", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_reconciliation_matches_org_id", table_name="reconciliation_matches")
    op.drop_table("reconciliation_matches")
    match_type.drop(op.get_bind())
