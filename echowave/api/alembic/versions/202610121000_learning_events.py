"""Training loop step 1: learning_events

Additive only: one table (services/training_loop). One row per thing that
happened to one agent's suggestion or action in one workspace, written only
while ``training_loop`` is on, so with the flag off it stays empty. Nothing
existing changes.

Downgrading drops the table.

Revision ID: 20261012learningevents
Revises: 20261011escalations
"""

import sqlalchemy as sa
from alembic import op

revision = "20261012learningevents"
down_revision = "20261011escalations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_events",
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
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("subject_key", sa.String(128), nullable=False),
        sa.Column("input_ref", sa.String(160), nullable=True),
        sa.Column("group_key", sa.String(160), nullable=True),
        sa.Column("input_text", sa.Text(), nullable=True),
        sa.Column("model_output", sa.Text(), nullable=True),
        sa.Column("owner_final", sa.Text(), nullable=True),
        sa.Column("detail", sa.String(200), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=True),
        sa.Column("consent_state", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "event_type",
            "subject_key",
            name="uq_learning_events_once",
        ),
    )
    op.create_index("ix_learning_events_id", "learning_events", ["id"], unique=False)
    op.create_index(
        "ix_learning_events_org_agent",
        "learning_events",
        ["organization_id", "workflow_id", "created_at"],
    )
    op.create_index(
        "ix_learning_events_org_type",
        "learning_events",
        ["organization_id", "event_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_learning_events_org_type", table_name="learning_events")
    op.drop_index("ix_learning_events_org_agent", table_name="learning_events")
    op.drop_index("ix_learning_events_id", table_name="learning_events")
    op.drop_table("learning_events")
