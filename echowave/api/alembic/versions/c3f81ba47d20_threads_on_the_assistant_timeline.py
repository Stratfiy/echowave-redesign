"""A thread id, so Decibyl's conversation can be more than one.

The assistant's thread was defined by absence: rows with neither a workflow
nor a folder. That gave every organisation exactly one conversation with
Decibyl, for ever, with nowhere to put a second -- so "start a new chat" was
not a button anybody could add, and older chats had nowhere to be listed.

Nullable, and nothing is backfilled. NULL means the thread the account has
always had, which is every row written before this and every row written by
anything that has not been taught about threads. A new chat gets an id; the
original keeps its absence. That way the migration cannot lose a
conversation and the old reads keep working unchanged.

Revision ID: c3f81ba47d20
Revises: b7e4d9a2c108
"""

import sqlalchemy as sa
from alembic import op

revision = "c3f81ba47d20"
down_revision = "b7e4d9a2c108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_events",
        sa.Column("thread_id", sa.String(length=36), nullable=True),
    )
    # The read is always "this organisation's rows in this thread, newest
    # first", which without the index walks the whole account's history to
    # find one conversation.
    op.create_index(
        "ix_agent_events_org_thread_at",
        "agent_events",
        ["organization_id", "thread_id", "at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_events_org_thread_at", table_name="agent_events")
    op.drop_column("agent_events", "thread_id")
