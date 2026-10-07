"""Push tokens for the native app (MOBILE.md, flag ``mobile_push``)

Additive only. One new table and nothing else: no existing table is changed
and nothing is backfilled, so downgrading drops exactly what this adds, and
with the flag off the table is never read or written.

* ``mobile_push_tokens`` -- one Expo push token per install of the iOS or
  Android app, owned by one person, with the workspace it registered in.

Revision ID: 20261009mobile
Revises: 20261009people
"""

import sqlalchemy as sa
from alembic import op

revision = "20261009mobile"
down_revision = "20261009people"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mobile_push_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("device_label", sa.String(80), nullable=True),
        sa.Column("app_version", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token", name="mobile_push_tokens_token_key"),
    )
    op.create_index("ix_mobile_push_tokens_user", "mobile_push_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_mobile_push_tokens_user", table_name="mobile_push_tokens")
    op.drop_table("mobile_push_tokens")
