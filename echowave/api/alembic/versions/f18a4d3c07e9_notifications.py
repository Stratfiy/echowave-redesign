"""Record of operational notices sent to customers.

One table with one job: stop a notice being sent twice. The jobs that send
these are safe to re-run by design — that is what makes a missed cron harmless
— and without a record, "safe to re-run" would mean a customer with a low
balance gets the same warning every time a worker restarts.

The unique constraint is on ``(organization_id, kind, dedupe_key)`` and the row
is claimed *before* the send, so two workers racing on the same account produce
one email rather than two.

Revision ID: f18a4d3c07e9
Revises: e5b27c0a91d4
"""

from alembic import op
import sqlalchemy as sa


revision = "f18a4d3c07e9"
down_revision = "e5b27c0a91d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column(
            "channel", sa.String(length=16), nullable=False, server_default="email"
        ),
        sa.Column("recipients", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column(
            "sent", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "kind", "dedupe_key", name="uq_notification_dedupe"
        ),
    )
    op.create_index("ix_notifications_id", "notifications", ["id"], unique=False)
    op.create_index(
        "ix_notifications_org",
        "notifications",
        ["organization_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_org", table_name="notifications")
    op.drop_index("ix_notifications_id", table_name="notifications")
    op.drop_table("notifications")
