"""The in-app inbox behind the bell.

Parented on e7b19c4d2a63 to keep this branch on a single head — see
api/tests/test_migration_heads.py.

Revision ID: f3a9d1c7e2b4
Revises: e7b19c4d2a63
"""

import sqlalchemy as sa
from alembic import op

revision = "f3a9d1c7e2b4"
down_revision = "e7b19c4d2a63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "in_app_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "kind", "dedupe_key", name="uq_in_app_notification"
        ),
    )
    op.create_index("ix_in_app_notifications_id", "in_app_notifications", ["id"])
    op.create_index(
        "ix_in_app_notifications_org",
        "in_app_notifications",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_in_app_notifications_org", table_name="in_app_notifications")
    op.drop_index("ix_in_app_notifications_id", table_name="in_app_notifications")
    op.drop_table("in_app_notifications")
