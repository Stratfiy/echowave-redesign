"""email verification: prove the address at signup

Revision ID: d9e4a71c83f2
Revises: c8d3f6a20b45
Create Date: 2026-08-13

The permanent fact (users.email_verified_at) is split from the transient
challenge (email_verification_challenges) because a code with an expiry and an
attempt counter is not a property of a person, and keeping it on the user row
would drag a dead credential into every read of an account.

email_verified_at is nullable and backfills to NULL, which is correct:
everybody who signed up before this shipped is unproved, and nothing hard-gates
on it. Locking out existing customers to enforce a rule introduced after they
joined is an outage, not a security improvement.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d9e4a71c83f2"
down_revision: Union[str, Sequence[str], None] = "c8d3f6a20b45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "email_verification_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_salt", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "send_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="_email_verification_user_uc"),
    )
    op.create_index(
        op.f("ix_email_verification_challenges_id"),
        "email_verification_challenges",
        ["id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_email_verification_challenges_id"),
        table_name="email_verification_challenges",
    )
    op.drop_table("email_verification_challenges")
    op.drop_column("users", "email_verified_at")
