"""Single-use password recovery and browser-session revocation.

Revision ID: f4c9b31a82de
Revises: e9b4c72a1f36
"""

import sqlalchemy as sa
from alembic import op

revision = "f4c9b31a82de"
down_revision = "e9b4c72a1f36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auth_version", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_table(
        "password_reset_challenges",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("send_count", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("password_reset_challenges")
    op.drop_column("users", "auth_version")
