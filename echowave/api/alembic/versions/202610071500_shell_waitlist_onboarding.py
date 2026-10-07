"""Waitlist requests and per-person onboarding (launch stream `shell`)

Two new tables, nothing changed in an existing one, so a downgrade only
drops what this added.

Revision ID: 202610071500shell
Revises: 202610071200auto
"""

import sqlalchemy as sa
from alembic import op

revision = "202610071500shell"
down_revision = "202610071200auto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "waitlist_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("first_task", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("occupation", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ux_waitlist_requests_email",
        "waitlist_requests",
        [sa.text("lower(email)")],
        unique=True,
    )
    op.create_table(
        "user_onboarding",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("timezone_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preferred_name", sa.String(length=80), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("user_onboarding")
    op.drop_index("ux_waitlist_requests_email", table_name="waitlist_requests")
    op.drop_table("waitlist_requests")
