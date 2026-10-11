"""Payment links agents created, watched until paid

Additive only: one table (``payment_links``, services/workflow/
payment_links.py). Each row is a Razorpay payment link an agent created on
the business's own account, with the conversation it belongs to, so the
payment can be confirmed back there.

Downgrading drops the table.

Revision ID: 20261014paymentlinks
Revises: 20261013instagram
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014paymentlinks"
down_revision = "20261013instagram"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(32), nullable=False, server_default="razorpay"),
        sa.Column("link_id", sa.String(64), nullable=False),
        sa.Column("short_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="INR"),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
        sa.Column("channel", sa.String(32), nullable=True),
        sa.Column("recipient", sa.String(128), nullable=True),
        sa.Column("connected_account_id", sa.String(128), nullable=True),
        sa.Column("acting_user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="created"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("organization_id", "link_id", name="uq_payment_links_link"),
    )
    op.create_index(
        "ix_payment_links_organization_id", "payment_links", ["organization_id"]
    )
    op.create_index("ix_payment_links_open", "payment_links", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_payment_links_open", table_name="payment_links")
    op.drop_index("ix_payment_links_organization_id", table_name="payment_links")
    op.drop_table("payment_links")
