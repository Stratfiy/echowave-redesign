"""One-time codes for setting a new password.

Parented on c4a81f2b6d30 to keep this branch on a single head — see
api/tests/test_migration_heads.py.

Revision ID: e7b19c4d2a63
Revises: c4a81f2b6d30
"""

import sqlalchemy as sa
from alembic import op

revision = "e7b19c4d2a63"
down_revision = "c4a81f2b6d30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_salt", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("send_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="_password_reset_user_uc"),
    )
    op.create_index(
        "ix_password_reset_challenges_id", "password_reset_challenges", ["id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_password_reset_challenges_id", table_name="password_reset_challenges"
    )
    op.drop_table("password_reset_challenges")
