"""organisation_facts: what an agent learned, kept apart from what the account told us

Revision ID: a7e2c95b1d38
Revises: f1c8d3b45e97
Create Date: 2026-09-12 10:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7e2c95b1d38"
down_revision: Union[str, None] = "f1c8d3b45e97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organisation_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_key", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source_run_id", sa.Integer(), nullable=True),
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_organisation_facts_id"), "organisation_facts", ["id"])
    op.create_index(
        "uq_organisation_facts_subject_key",
        "organisation_facts",
        ["organization_id", "subject_type", "subject_key", "key"],
        unique=True,
    )
    op.create_index(
        "ix_organisation_facts_lookup",
        "organisation_facts",
        ["organization_id", "subject_type", "subject_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_organisation_facts_lookup", table_name="organisation_facts")
    op.drop_index("uq_organisation_facts_subject_key", table_name="organisation_facts")
    op.drop_index(op.f("ix_organisation_facts_id"), table_name="organisation_facts")
    op.drop_table("organisation_facts")
