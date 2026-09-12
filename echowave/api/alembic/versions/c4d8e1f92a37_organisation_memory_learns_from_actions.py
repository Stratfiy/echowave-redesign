"""An organisation's memory learns from what its agents did, and waits to be believed

Adds three columns to ``organisation_facts``.

``kind`` separates what we now know from what a caller wanted and no agent
could answer. Both belong on the same screen -- "we do not know our Saturday
hours" is a fact about the business seen from the other side -- so they share a
table rather than splitting one question across two pages.

``status`` and ``confirmed_at`` are the safety rail. Everything inferred from a
conversation arrives as ``learned`` and a learned fact never reaches an agent's
prompt; a person confirms it first. An agent that starts confidently telling
callers something it merely overheard is how an account is lost, and no
corroboration count substitutes for somebody saying yes.

Existing rows backfill to ``fact``/``learned`` deliberately rather than to
``confirmed``. They were inferred by a model from a conversation and nobody has
ever been shown them; marking them confirmed would promote every guess this
table has ever made into an agent's prompt in one migration.

Revision ID: c4d8e1f92a37
Revises: b3f7a2d61c84
"""

import sqlalchemy as sa
from alembic import op

revision = "c4d8e1f92a37"
down_revision = "b3f7a2d61c84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organisation_facts",
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="fact",
        ),
    )
    op.add_column(
        "organisation_facts",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="learned",
        ),
    )
    op.add_column(
        "organisation_facts",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The screen reads one organisation's memory ordered by how often each
    # thing has been seen, so that is the index it gets.
    op.create_index(
        "ix_organisation_facts_org_kind_status",
        "organisation_facts",
        ["organization_id", "kind", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organisation_facts_org_kind_status", table_name="organisation_facts"
    )
    op.drop_column("organisation_facts", "confirmed_at")
    op.drop_column("organisation_facts", "status")
    op.drop_column("organisation_facts", "kind")
