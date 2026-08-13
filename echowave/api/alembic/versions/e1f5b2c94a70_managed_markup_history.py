"""managed markup: an effective-dated history, and a confirmation challenge

The multiple charged on managed model usage moves from an environment variable
to a value an operator can change without a deploy. Two tables, for two
reasons.

``managed_markup_history`` is effective-dated and never updated in place, like
``organization_rate_history``: re-costing a call from March has to reproduce
what that customer was actually charged, and a mutable setting makes that
impossible the first time anyone edits it. The partial unique index enforces
that exactly one markup is in force at a time — two concurrent saves would
otherwise both close the old row and both open a new one.

``markup_change_challenges`` holds a staged change and a hashed code. The
proposed value lives here rather than in the confirming request, so a replayed
or tampered confirmation cannot apply a different multiple than the one that
was emailed about.

No data migration. With both tables empty the resolver falls back to
``MANAGED_PROVIDER_MARKUP_BPS``, so behaviour is unchanged until the first edit.

Revision ID: e1f5b2c94a70
Revises: d9e4a71c83f2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f5b2c94a70"
down_revision: Union[str, Sequence[str], None] = "d9e4a71c83f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "managed_markup_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("markup_bps", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("set_by", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["set_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_managed_markup_history_id"),
        "managed_markup_history",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_managed_markup_effective",
        "managed_markup_history",
        ["effective_from"],
        unique=False,
    )
    # At most one open row. Partial, so closed rows do not collide.
    op.create_index(
        "uq_managed_markup_open",
        "managed_markup_history",
        ["effective_to"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )

    op.create_table(
        "markup_change_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("markup_bps", sa.Integer(), nullable=False),
        sa.Column("previous_markup_bps", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_salt", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_markup_change_challenges_id"),
        "markup_change_challenges",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_markup_change_challenges_id"),
        table_name="markup_change_challenges",
    )
    op.drop_table("markup_change_challenges")
    op.drop_index("uq_managed_markup_open", table_name="managed_markup_history")
    op.drop_index("ix_managed_markup_effective", table_name="managed_markup_history")
    op.drop_index(
        op.f("ix_managed_markup_history_id"), table_name="managed_markup_history"
    )
    op.drop_table("managed_markup_history")
