"""verified_numbers: numbers an organization proved it can answer

Revision ID: c8d3f6a20b45
Revises: b7c2e5f19a34
Create Date: 2026-08-13

Separate from telephony_phone_numbers, which is for numbers we rent to a
customer and bind to an inbound workflow. This is the opposite direction: a
number the customer already owns, proved by answering an SMS, so we are willing
to dial it for a test call.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8d3f6a20b45"
down_revision: Union[str, Sequence[str], None] = "b7c2e5f19a34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "verified_numbers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        # Salted, not bare SHA-256: a six-digit code has a search space of one
        # million, so an unsalted digest is a rainbow-table lookup.
        sa.Column("code_hash", sa.String(length=64), nullable=True),
        sa.Column("code_salt", sa.String(length=32), nullable=True),
        sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "send_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "phone_number", name="_verified_number_org_number_uc"
        ),
    )
    op.create_index(
        "ix_verified_numbers_org_number",
        "verified_numbers",
        ["organization_id", "phone_number"],
    )
    op.create_index(op.f("ix_verified_numbers_id"), "verified_numbers", ["id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_verified_numbers_id"), table_name="verified_numbers")
    op.drop_index("ix_verified_numbers_org_number", table_name="verified_numbers")
    op.drop_table("verified_numbers")
