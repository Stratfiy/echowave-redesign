"""Learning follow-ups: learning_events.scope and archived_at

Additive only, on the table #596 added (services/training_loop):

* ``scope``: ``agent`` (an agent's own event, the default for every existing
  row) or ``decibyl`` (a thumb on Decibyl's own thread, which has no agent).
* ``data``: structured facts of an event as JSON (a routing decision's
  candidates, chosen model, latency, tokens, cost and later outcomes). No
  free text: words go in the redacted text columns.
* ``archived_at``: set when a workspace chose "Stop and delete". An archived
  row is excluded from export, counts and training; it is never hard-deleted
  by this change.

Downgrading drops the three columns (and so forgets which rows were archived).

Revision ID: 20261013learningfollowups
Revises: 20261012learningevents
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261013learningfollowups"
down_revision = "20261012learningevents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "learning_events",
        sa.Column("scope", sa.String(12), nullable=False, server_default="agent"),
    )
    op.add_column(
        "learning_events",
        sa.Column("data", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "learning_events",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("learning_events", "archived_at")
    op.drop_column("learning_events", "data")
    op.drop_column("learning_events", "scope")
