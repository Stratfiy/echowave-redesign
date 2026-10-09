"""Call me when it's done (services/call_when_done)

Additive only:

* ``done_calls``: one call to one person, with its state; at most one
  ``queued`` per person (partial unique index), which is what collapses
  tasks that finish together into one call.
* ``done_callbacks``: "call me when this is done", and what it settled on.
* ``done_call_numbers``: the number a person confirmed for these calls.
* ``member_preferences.call_when_done``: the standing preference.

Read only while ``call_when_done`` is on, so with the flag off it is inert.
Downgrading drops all of it.

Revision ID: 20261010callwhendone
Revises: 20261009phase3staff
"""

import sqlalchemy as sa
from alembic import op

revision = "20261010callwhendone"
down_revision = "20261009phase3staff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "done_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("notified", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_done_calls_due", "done_calls", ["state", "due_at"])
    op.create_index(
        "uq_done_calls_one_queued",
        "done_calls",
        ["organization_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("state = 'queued'"),
    )

    op.create_table(
        "done_callbacks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("subject", sa.String(80), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("standing", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("finished_key", sa.String(80), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("needs_you", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "call_id",
            sa.Integer(),
            sa.ForeignKey("done_calls.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_done_callbacks_watch",
        "done_callbacks",
        ["organization_id", "state", "subject"],
    )
    op.create_index(
        "uq_done_callbacks_finished",
        "done_callbacks",
        ["organization_id", "user_id", "finished_key"],
        unique=True,
        postgresql_where=sa.text("finished_key IS NOT NULL"),
    )

    op.create_table(
        "done_call_numbers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("card_event_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_done_call_number_person"
        ),
    )

    op.add_column(
        "member_preferences",
        sa.Column("call_when_done", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("member_preferences", "call_when_done")
    op.drop_table("done_call_numbers")
    op.drop_index("uq_done_callbacks_finished", table_name="done_callbacks")
    op.drop_index("ix_done_callbacks_watch", table_name="done_callbacks")
    op.drop_table("done_callbacks")
    op.drop_index("uq_done_calls_one_queued", table_name="done_calls")
    op.drop_index("ix_done_calls_due", table_name="done_calls")
    op.drop_table("done_calls")
