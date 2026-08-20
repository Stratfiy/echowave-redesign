"""managed bundles

The Simple picker's cards, as rows: what each one is called, which tiers it
runs on, whether it is offered and in what order.

Trimmed by hand to this table. Autogenerate again proposed dropping two partial
indexes it cannot round-trip and a handful of server defaults it reads
differently from the models — pre-existing drift, none of it this change.

Revision ID: c80e59fe8b0c
Revises: aea73cbd42e9
Create Date: 2026-08-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c80e59fe8b0c"
down_revision: Union[str, None] = "aea73cbd42e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "managed_bundles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("blurb", sa.String(length=240), nullable=False, server_default=""),
        sa.Column("architecture", sa.String(length=16), nullable=False),
        sa.Column("stt_tier", sa.String(length=32), nullable=True),
        sa.Column("tts_tier", sa.String(length=32), nullable=True),
        sa.Column("llm_tier", sa.String(length=32), nullable=True),
        sa.Column("realtime_tier", sa.String(length=32), nullable=True),
        sa.Column(
            "display_order", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        # The slug is what an agent's configuration records, so it has to be
        # one thing. Two rows sharing one would make which bundle an agent runs
        # depend on which the query returned first.
        sa.UniqueConstraint("slug", name="uq_managed_bundle_slug"),
    )
    op.create_index(op.f("ix_managed_bundles_id"), "managed_bundles", ["id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_managed_bundles_id"), table_name="managed_bundles")
    op.drop_table("managed_bundles")
