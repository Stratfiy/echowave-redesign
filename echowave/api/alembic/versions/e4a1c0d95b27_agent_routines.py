"""A bot that starts its own work

``agent_routines``.

Everything else in the schema records something a person or a caller started.
This is the first table that starts things itself -- a standing instruction to
run a bot at a time, with nobody waiting on the other end. That absence is the
design problem the columns answer: a routine that fires at the wrong time,
twice, or not at all has no caller to notice and no transcript to explain
itself.

So the schedule is stored in pieces rather than as a cron string. Four
cadences and three anchors, because the person setting this runs a clinic and
the difference between ``0 9 * * 1-5`` and ``0 9 * * 1,5`` is a support ticket
waiting to happen. The anchor is the column that earns its place: "every
morning" means when the business opens, and a stored 09:30 goes quietly wrong
the week a clinic moves to 10:00 -- the report still arrives, an hour before
anybody is there to read it, and nothing says the schedule is now wrong.

``is_active`` defaults false and ``tested_at`` starts NULL, so a routine
cannot arm until it has been test-run once. The first time a Desk runs
unsupervised it writes into somebody's real accounting software.

``last_fired_at`` stores the *slot* that fired rather than the moment of
firing, which is what makes a minute tick safe: a tick that runs twice, or a
worker that comes back up inside the catch-up window, compares against the
slot and declines to send the same report twice.

Creates the table and nothing else: instant on a live database, and it cannot
lock anything a call is using.

Revision ID: e4a1c0d95b27
Revises: c7e2b9a41f36
"""

import sqlalchemy as sa
from alembic import op

revision = "e4a1c0d95b27"
down_revision = "c7e2b9a41f36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_routines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "cadence", sa.String(length=16), nullable=False, server_default="daily"
        ),
        sa.Column(
            "anchor", sa.String(length=16), nullable=False, server_default="opening"
        ),
        sa.Column("at_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("offset_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weekday", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "needs_apps",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_skipped_reason", sa.String(length=32), nullable=True),
        sa.Column("last_skipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        # CASCADE: a deleted bot's standing instructions must not outlive it
        # and keep firing against nothing.
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_routines_id", "agent_routines", ["id"])
    op.create_index(
        "ix_agent_routines_organization_id", "agent_routines", ["organization_id"]
    )
    op.create_index("ix_agent_routines_workflow_id", "agent_routines", ["workflow_id"])
    # The tick's only query: every armed routine, across all tenants, once a
    # minute. Partial so it holds the handful switched on rather than every
    # routine anybody ever drafted.
    op.create_index(
        "ix_agent_routines_active",
        "agent_routines",
        ["is_active"],
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_agent_routines_org_workflow",
        "agent_routines",
        ["organization_id", "workflow_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_routines_org_workflow", table_name="agent_routines")
    op.drop_index("ix_agent_routines_active", table_name="agent_routines")
    op.drop_index("ix_agent_routines_workflow_id", table_name="agent_routines")
    op.drop_index("ix_agent_routines_organization_id", table_name="agent_routines")
    op.drop_index("ix_agent_routines_id", table_name="agent_routines")
    op.drop_table("agent_routines")
