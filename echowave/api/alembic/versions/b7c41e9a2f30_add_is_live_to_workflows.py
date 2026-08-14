"""Add is_live to workflows: whether an agent takes calls right now

Distinct from ``status``, and deliberately so. ``status`` is a lifecycle —
active or archived, i.e. whether the agent is in the list at all. ``is_live``
is an operational switch: this agent exists, you can see it and edit it, and it
is or is not answering the phone.

Folding this into ``status`` as a third value would have hidden a paused agent
from every screen that filters ``status == 'active'``, including the one you
would use to unpause it.

Existing agents backfill to live. Anything else would silently stop answering
every phone number already pointed at one, on deploy.
"""

from alembic import op
import sqlalchemy as sa

revision = "b7c41e9a2f30"
down_revision = "e1f5b2c94a70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "is_live",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # Partial index: the inbound path asks "is this one live" per call, and the
    # rows it asks about are overwhelmingly live ones.
    op.create_index(
        "ix_workflows_is_live",
        "workflows",
        ["is_live"],
        postgresql_where=sa.text("is_live = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_workflows_is_live", table_name="workflows")
    op.drop_column("workflows", "is_live")
