"""A bot can post its events to a URL somebody pasted in

One row per bot: where to send, what to send, and the secret the receiver
verifies the signature with.

Unique on workflow_id because the field on the screen is singular. A bot
with two destinations is a fan-out somebody wants eventually, and adding a
second row later is a smaller change than taking one away from anybody who
had come to rely on it.

The delivery side reuses ``webhook_deliveries`` -- the engine there already
claims atomically, backs off, dead-letters and has a sweeper, and a second
copy of that is a second set of bugs. Two columns stood in the way:
``workflow_run_id`` was NOT NULL, and the per-run/per-node unique constraint
is what makes a retried run stop short of double-sending. An event has no
run, so the column is nullable now and a partial unique index covers exactly
the rows it does not: one delivery per bot per event, which is the same
promise in the shape an event has.

Revision ID: d4a07c1b93e2
Revises: c3f81ba47d20
"""

import sqlalchemy as sa
from alembic import op

revision = "d4a07c1b93e2"
down_revision = "c3f81ba47d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_event_webhooks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2048), nullable=False),
        # The signing secret. Generated here, shown to the person once, and
        # never sent out again: a receiver that cannot verify a signature is
        # a receiver that will take anybody's POST.
        sa.Column("secret", sa.String(length=64), nullable=False),
        # Which kinds go out. Empty means the ones a person can subscribe to
        # on the bell, resolved at send time rather than copied in here, so a
        # new notifiable kind reaches an existing webhook.
        sa.Column("kinds", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workflow_id", name="uq_bot_event_webhooks_workflow"),
    )
    op.create_index(
        "ix_bot_event_webhooks_organization_id",
        "bot_event_webhooks",
        ["organization_id"],
    )

    # An event has no run, so the run is optional. Existing rows all have one
    # and the existing constraint still governs them.
    op.alter_column(
        "webhook_deliveries",
        "workflow_run_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    # The dedupe for rows with no run. A NULL is distinct under the existing
    # unique constraint, which is why that constraint cannot be the one doing
    # this job -- the comment on the model says so, and it is the reason this
    # index exists rather than a widened constraint.
    op.create_index(
        "uq_webhook_deliveries_bot_event",
        "webhook_deliveries",
        ["workflow_id", "webhook_node_id"],
        unique=True,
        postgresql_where=sa.text("workflow_run_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_webhook_deliveries_bot_event", table_name="webhook_deliveries")
    # Rows with no run cannot satisfy the NOT NULL that comes back, and they
    # are this feature's own deliveries, so they go with it.
    op.execute("DELETE FROM webhook_deliveries WHERE workflow_run_id IS NULL")
    op.drop_column("webhook_deliveries", "workflow_id")
    op.alter_column(
        "webhook_deliveries",
        "workflow_run_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_index(
        "ix_bot_event_webhooks_organization_id", table_name="bot_event_webhooks"
    )
    op.drop_table("bot_event_webhooks")
