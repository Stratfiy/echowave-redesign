"""account invitations: a way to claim an account somebody else created

Until now the only doors in were self-serve signup and Google first sign-in,
both of which are the new user acting for themselves. An owner could not add a
colleague, and staff could not stand up an account for a customer — because a
user row created by someone else has no password, and ``login`` deliberately
refuses any account whose ``password_hash`` is falsy.

This table is the challenge that lets them set one. Salted-hashed code, TTL and
attempt ceiling, exactly like the email-verification and markup challenges.

``email`` is unique rather than merely indexed: one live invitation per address
is the property that makes a resend mean one valid code instead of two, and it
is worth having the database hold it rather than the service.

``organization_id`` is nullable on purpose. An owner inviting a colleague names
their own organization; staff creating a new customer names none, and one is
provisioned on acceptance.

Revision ID: a3c81d05e6f2
Revises: e1f5b2c94a70
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3c81d05e6f2"
down_revision: Union[str, Sequence[str], None] = "e1f5b2c94a70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_salt", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("invited_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_invitations_id"), "account_invitations", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_account_invitations_email"),
        "account_invitations",
        ["email"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_account_invitations_email"), table_name="account_invitations"
    )
    op.drop_index(op.f("ix_account_invitations_id"), table_name="account_invitations")
    op.drop_table("account_invitations")
