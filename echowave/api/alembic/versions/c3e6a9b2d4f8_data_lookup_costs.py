"""Bought lookups: vendor cost beside the charge, off a call (D-1b)

A web search Decibyl runs from the thread on the platform's key has no run
to hang a receipt line on. This table is its ``call_cost_items``: written in
the same transaction as the ledger debit, read by the unit-economics screen.

Revision ID: c3e6a9b2d4f8
Revises: b2d5f8a1c3e7
"""

import sqlalchemy as sa
from alembic import op

revision = "c3e6a9b2d4f8"
down_revision = "b2d5f8a1c3e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_lookup_costs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "vendor_cost_paise", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("charged_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ref_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_data_lookup_costs_id", "data_lookup_costs", ["id"])
    op.create_index(
        "ix_data_lookup_costs_org_created",
        "data_lookup_costs",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "uq_data_lookup_costs_org_ref",
        "data_lookup_costs",
        ["organization_id", "ref_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("data_lookup_costs")
