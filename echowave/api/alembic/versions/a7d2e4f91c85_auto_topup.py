"""Auto top-up settings and the attempt ledger.

Parented on c1f8a3d70b96 deliberately: this branch already carries the oauth2
credential type, and a second head is what stops `alembic upgrade head` during
a deploy. See api/tests/test_migration_heads.py.

Revision ID: a7d2e4f91c85
Revises: c1f8a3d70b96
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d2e4f91c85"
down_revision = "c1f8a3d70b96"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auto_topup_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("trigger_days", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "trigger_paise", sa.BigInteger(), nullable=False, server_default="15000"
        ),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "monthly_cap_paise", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("max_per_month", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # One policy per account. Two rows would mean two answers to "is this
        # on", and the sweep would pick whichever it read first.
        sa.UniqueConstraint("organization_id"),
    )
    op.create_index(
        op.f("ix_auto_topup_settings_id"), "auto_topup_settings", ["id"], unique=False
    )

    op.create_table(
        "auto_topup_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=24), nullable=False, server_default="scheduled"
        ),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("charge_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("charged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_auto_topup_attempts_id"), "auto_topup_attempts", ["id"], unique=False
    )
    op.create_index(
        "ix_auto_topup_attempts_org",
        "auto_topup_attempts",
        ["organization_id", "status"],
        unique=False,
    )
    # The double-charge guard, made structural. The decision engine also
    # refuses, but it runs in a worker that can be running twice; a partial
    # unique index cannot be raced.
    op.create_index(
        "uq_auto_topup_attempts_in_flight",
        "auto_topup_attempts",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('scheduled', 'charging')"),
    )


def downgrade() -> None:
    op.drop_index("uq_auto_topup_attempts_in_flight", table_name="auto_topup_attempts")
    op.drop_index("ix_auto_topup_attempts_org", table_name="auto_topup_attempts")
    op.drop_index(op.f("ix_auto_topup_attempts_id"), table_name="auto_topup_attempts")
    op.drop_table("auto_topup_attempts")
    op.drop_index(op.f("ix_auto_topup_settings_id"), table_name="auto_topup_settings")
    op.drop_table("auto_topup_settings")
