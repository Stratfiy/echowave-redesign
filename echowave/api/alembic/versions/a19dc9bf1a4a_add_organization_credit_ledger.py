"""add organization credit ledger

Revision ID: a19dc9bf1a4a
Revises: 00b0201ad918
Create Date: 2026-07-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a19dc9bf1a4a"
down_revision: Union[str, None] = "00b0201ad918"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_credit_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "entry_type",
            sa.Enum(
                "purchase",
                "charge",
                "trial_grant",
                "adjustment",
                "refund",
                name="credit_ledger_entry_type",
            ),
            nullable=False,
        ),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("balance_after_usd", sa.Float(), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_organization_credit_ledger_id"),
        "organization_credit_ledger",
        ["id"],
        unique=False,
    )
    op.create_index(
        "idx_credit_ledger_org_created",
        "organization_credit_ledger",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_credit_ledger_workflow_run_id",
        "organization_credit_ledger",
        ["workflow_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_credit_ledger_workflow_run_id", table_name="organization_credit_ledger"
    )
    op.drop_index(
        "idx_credit_ledger_org_created", table_name="organization_credit_ledger"
    )
    op.drop_index(
        op.f("ix_organization_credit_ledger_id"),
        table_name="organization_credit_ledger",
    )
    op.drop_table("organization_credit_ledger")
    op.execute("DROP TYPE IF EXISTS credit_ledger_entry_type")
