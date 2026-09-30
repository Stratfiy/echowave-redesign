"""organisation_facts.user_id: a member's personal memory (MEM-1).

The workspace index now covers only rows with no member, and a second partial
index gives each member their own identity for the same fact. Existing rows all
have user_id NULL, so the rebuilt workspace index holds exactly what it did.

Revision ID: e8b4d6f2a1c9
Revises: d7a3c5e9f1b2
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8b4d6f2a1c9"
down_revision: Union[str, None] = "d7a3c5e9f1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organisation_facts",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.drop_index("uq_organisation_facts_subject_key", table_name="organisation_facts")
    op.create_index(
        "uq_organisation_facts_subject_key",
        "organisation_facts",
        ["organization_id", "subject_type", "subject_key", "key"],
        unique=True,
        postgresql_where=sa.text("workflow_id IS NULL AND user_id IS NULL"),
    )
    op.create_index(
        "uq_organisation_facts_member_subject_key",
        "organisation_facts",
        ["organization_id", "user_id", "subject_type", "subject_key", "key"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Personal rows have no home once the column goes; drop them first so the
    # restored workspace index cannot collide with a member's copy of a fact.
    op.execute("DELETE FROM organisation_facts WHERE user_id IS NOT NULL")
    op.drop_index(
        "uq_organisation_facts_member_subject_key", table_name="organisation_facts"
    )
    op.drop_index("uq_organisation_facts_subject_key", table_name="organisation_facts")
    op.create_index(
        "uq_organisation_facts_subject_key",
        "organisation_facts",
        ["organization_id", "subject_type", "subject_key", "key"],
        unique=True,
        postgresql_where=sa.text("workflow_id IS NULL"),
    )
    op.drop_column("organisation_facts", "user_id")
