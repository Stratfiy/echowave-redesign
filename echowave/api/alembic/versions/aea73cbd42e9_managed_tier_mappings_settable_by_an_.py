"""managed tier mappings, settable by an operator

Which vendor and model serve a managed tier stops being a constant in
``managed_tiers.py`` and becomes a row somebody can change.

Trimmed by hand to just this table. Autogenerate also proposed dropping two
partial indexes whose ``postgresql_where`` it renders in a form it then fails
to recognise on the next run, plus a handful of server-default differences it
reads differently from the models. All pre-existing drift, none of it this
change.

Revision ID: aea73cbd42e9
Revises: 548ce08a26ac
Create Date: 2026-08-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aea73cbd42e9"
down_revision: Union[str, None] = "548ce08a26ac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "managed_tier_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("component", sa.String(length=32), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        # One mapping per tier. Two rows for the same tier would make which
        # vendor answers the phone depend on which the query returned first.
        sa.UniqueConstraint("component", "tier", name="uq_managed_tier_mapping"),
    )
    op.create_index(
        op.f("ix_managed_tier_mappings_id"), "managed_tier_mappings", ["id"]
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_managed_tier_mappings_id"), table_name="managed_tier_mappings"
    )
    op.drop_table("managed_tier_mappings")
