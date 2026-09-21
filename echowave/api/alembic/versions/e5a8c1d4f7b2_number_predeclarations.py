"""Pre-declaration of calling numbers to the telecom provider (FD-2)

TRAI's TCCCP Third Amendment requires a number used for automated voice
calls in India to be declared to the provider first. This is the account's
record of that: one row per number per workspace, keyed on the number
because a provider's calling numbers live in its configuration's
credentials and need not have a phone-number row.

Revision ID: e5a8c1d4f7b2
Revises: d4f7b0c3e5a9
"""

import sqlalchemy as sa
from alembic import op

revision = "e5a8c1d4f7b2"
down_revision = "d4f7b0c3e5a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "number_predeclarations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("address_normalized", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "declared_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_number_predeclarations_id", "number_predeclarations", ["id"])
    op.create_index(
        "uq_number_predeclarations_org_number",
        "number_predeclarations",
        ["organization_id", "address_normalized"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("number_predeclarations")
