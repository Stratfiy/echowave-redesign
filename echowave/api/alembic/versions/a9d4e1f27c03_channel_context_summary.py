"""A channel's rolling summary, so its context compacts instead of dropping

``folders.context_summary`` and ``folders.context_summarised_through``.

A bot answering in a channel reads that channel's recent conversation as
context. The first cut of that took the newest thirty rows and dropped the
rest, which is the wrong direction for a room where somebody said "it's 500,
not 400" six weeks ago and nobody has said otherwise since. Dropping the oldest
is lossy in exactly the way a teammate who joined last week is lossy.

So the old end is folded into a summary rather than discarded -- the shape
Claude Code's own context uses, and the shape ``qa/analysis.py`` already uses
per node: keep a précis of everything before the window, keep the window
verbatim, and fold the window's oldest end into the précis when it overflows.
Each fold's input is one summary plus one batch, so the cost stays roughly
constant however long the channel has run.

``context_summarised_through`` is a watermark on ``agent_events.id``: every row
at or below it is covered by the summary and is not shown verbatim. A
watermark rather than a timestamp because the fold is over ids in insert
order, and the thing that must never happen is a row that is neither in the
summary nor in the window.

Both nullable. A channel that has never overflowed has no summary and no
watermark, and reads exactly as it did before this migration.
"""

import sqlalchemy as sa
from alembic import op

revision = "a9d4e1f27c03"
down_revision = "f2c6a83e70d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("folders", sa.Column("context_summary", sa.Text(), nullable=True))
    op.add_column(
        "folders",
        sa.Column("context_summarised_through", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("folders", "context_summarised_through")
    op.drop_column("folders", "context_summary")
