"""One timeline every screen reads from

``agent_events``.

Runs, tool actions and outcomes lived in three tables at three different
grains, so ``agent_activity`` is three separate queries and "what did this
agent do" could not be answered without hand-written SQL. A customer asked why
their agent refused a free noon slot and the answer took an SSH session and a
psql prompt.

Append-only, and additive: ``app_interactions`` and ``workflow_runs`` keep
their jobs -- one is the support record of what a tool returned, the other the
billing and state record of a call. This is the narrative, written alongside
them, carrying the sentence a person reads rather than the fields a screen
would have to assemble.

Four read paths, one per index, all ordered by time descending because every
screen is "newest first": one call, one bot, one team, one organisation. The
team column is denormalised at write time on purpose -- a bot moved between
teams later must not rewrite its own history.

No backfill in this migration. It creates the table and nothing else, so it is
instant on a live database and cannot lock anything a call is using. History is
reconstructed separately, by a script that can be run, checked and re-run.

Revision ID: c7e2b9a41f36
Revises: b3f81c2ea47d
"""

import sqlalchemy as sa
from alembic import op

revision = "c7e2b9a41f36"
down_revision = "b3f81c2ea47d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Nullable on purpose: a routine tick has no call, a credit top-up has
        # no bot, and a bot may be in no team. Demanding them would push
        # writers into inventing values.
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "definition_id",
            sa.Integer(),
            sa.ForeignKey("workflow_definitions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "folder_id",
            sa.Integer(),
            sa.ForeignKey("folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        # VARCHAR rather than a Postgres ENUM so a kind added next year needs
        # no migration, and an unrecognised one read back from an older writer
        # renders as itself rather than breaking the screen.
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column(
            "payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "is_deliverable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="always",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_agent_events_org_at", "agent_events", ["organization_id", "at"])
    op.create_index(
        "ix_agent_events_workflow_at", "agent_events", ["workflow_id", "at"]
    )
    op.create_index("ix_agent_events_run_at", "agent_events", ["workflow_run_id", "at"])
    op.create_index("ix_agent_events_folder_at", "agent_events", ["folder_id", "at"])
    # Partial: the deliverables list is "this organisation's cards, newest
    # first", which without this walks every event ever recorded to find the
    # few that are cards.
    op.create_index(
        "ix_agent_events_org_deliverables",
        "agent_events",
        ["organization_id", "at"],
        postgresql_where=sa.text("is_deliverable"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_events_org_deliverables", table_name="agent_events")
    op.drop_index("ix_agent_events_folder_at", table_name="agent_events")
    op.drop_index("ix_agent_events_run_at", table_name="agent_events")
    op.drop_index("ix_agent_events_workflow_at", table_name="agent_events")
    op.drop_index("ix_agent_events_org_at", table_name="agent_events")
    op.drop_table("agent_events")
