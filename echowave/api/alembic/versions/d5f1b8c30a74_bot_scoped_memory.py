"""A bot's own memory, inside the organisation's

``organisation_facts.workflow_id``.

Every fact belonged to the whole organisation, so there was no such thing as
one bot's memory and no way to say which bot had been told something. One
nullable column rather than a second table: the confirm gate, ``source_run_id``
traceability, corroboration count and uniqueness rule are needed identically at
both scopes, and two tables would mean two copies of the gate -- the first
thing that would diverge.

NULL means the organisation knows it and every bot reads it. A workflow id
means that bot alone. CASCADE from ``workflows`` because a deleted bot's
private memory should not outlive it; an organisation fact has no workflow and
is untouched.

THE UNIQUE INDEX IS THE WHOLE RISK HERE, and it is why this migration replaces
one rather than adding a column beside it. In Postgres NULLs are distinct in a
unique index, so simply adding ``workflow_id`` to the existing four-column
index would have stopped it constraining organisation facts at all: every
repeated fact about the business would have become a second row, with no error
and no symptom until a prompt filled with duplicates. Two partial indexes
instead -- one per scope -- each of which actually constrains its own scope.

``coalesce(workflow_id, 0)`` in a single index would also have been correct,
but an upsert's ON CONFLICT inference then has to reproduce that expression
exactly, and a bound parameter in place of the literal 0 fails to match
silently. An ``IS NULL`` predicate renders with no parameters, so the inference
either matches or Postgres refuses the statement outright.

Written as a drop-then-create with the unique index built first under its new
name: on a table this size it is instant, and the window in which neither
constraint exists is inside one transaction.

Revision ID: d5f1b8c30a74
Revises: e4a1c0d95b27
"""

import sqlalchemy as sa
from alembic import op

revision = "d5f1b8c30a74"
down_revision = "e4a1c0d95b27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organisation_facts",
        sa.Column("workflow_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_organisation_facts_workflow_id",
        "organisation_facts",
        "workflows",
        ["workflow_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Every existing row is an organisation fact, so the old index and the new
    # organisation-scoped one constrain exactly the same rows. Dropping the old
    # one first keeps the name free.
    op.drop_index("uq_organisation_facts_subject_key", table_name="organisation_facts")
    op.create_index(
        "uq_organisation_facts_subject_key",
        "organisation_facts",
        ["organization_id", "subject_type", "subject_key", "key"],
        unique=True,
        postgresql_where=sa.text("workflow_id IS NULL"),
    )
    op.create_index(
        "uq_organisation_facts_bot_subject_key",
        "organisation_facts",
        ["organization_id", "workflow_id", "subject_type", "subject_key", "key"],
        unique=True,
        postgresql_where=sa.text("workflow_id IS NOT NULL"),
    )
    op.create_index(
        "ix_organisation_facts_workflow",
        "organisation_facts",
        ["organization_id", "workflow_id"],
        postgresql_where=sa.text("workflow_id IS NOT NULL"),
    )


def downgrade() -> None:
    # A bot fact has no home in the old shape, and leaving it behind would
    # collide with the organisation fact of the same subject and key the moment
    # the unconditional unique index is rebuilt. Deleting is the only honest
    # downgrade, and it is what CASCADE would have done had the bot gone.
    op.execute("DELETE FROM organisation_facts WHERE workflow_id IS NOT NULL")

    op.drop_index("ix_organisation_facts_workflow", table_name="organisation_facts")
    op.drop_index(
        "uq_organisation_facts_bot_subject_key", table_name="organisation_facts"
    )
    op.drop_index("uq_organisation_facts_subject_key", table_name="organisation_facts")
    op.create_index(
        "uq_organisation_facts_subject_key",
        "organisation_facts",
        ["organization_id", "subject_type", "subject_key", "key"],
        unique=True,
    )

    op.drop_constraint(
        "fk_organisation_facts_workflow_id",
        "organisation_facts",
        type_="foreignkey",
    )
    op.drop_column("organisation_facts", "workflow_id")
