"""The agent a prospect hears before hiring a role

``is_demo`` on ``workflows``.

On the agent rather than on a phone number. The agent is the thing being
demonstrated, and there are two ways to reach it: the share link, which needs
no telephony and whose text chat works on a locked-down network where WebRTC
never connects, and a number pointed at it, which is stronger proof for a
product whose pitch is that it answers your phone. One flag, both derived -- so
a number is nice to have rather than the thing that gates a listing.

Default false. No agent becomes the public demo by migration.

Revision ID: d7a1c3e58b92
Revises: c4d8e1f92a37
"""

import sqlalchemy as sa
from alembic import op

revision = "d7a1c3e58b92"
down_revision = "c4d8e1f92a37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "is_demo", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    # The shelf reads this on every load, and one or two rows match out of
    # however many agents the platform holds.
    op.create_index(
        "ix_workflows_is_demo",
        "workflows",
        ["is_demo"],
        postgresql_where=sa.text("is_demo"),
    )


def downgrade() -> None:
    op.drop_index("ix_workflows_is_demo", table_name="workflows")
    op.drop_column("workflows", "is_demo")
