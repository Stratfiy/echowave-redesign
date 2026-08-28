"""Per-model markup override, alongside the global managed markup.

``managed_markup_history`` sets one multiple for every managed STT/LLM/TTS/
telephony line on every account. This adds a narrower table for a single
``(component, provider, model)`` — a model that is unusually cheap or
expensive to us relative to what the blanket markup would charge for it —
without moving anyone else's bill.

Keyed exactly like ``provider_rates``: ``model = ''`` is a provider-wide
override, and a model-specific row wins over it when both exist. Same
partial-unique-index pattern as every other effective-dated rate table here —
at most one open row per key, so a change closes the old row and opens a new
one rather than updating in place, which is what lets an old call re-cost to
the multiple that actually applied.

No OTP table alongside this one, unlike ``managed_markup_history``'s
``markup_change_challenges``: a single-line override cannot move every
account's bill the way the global value can, so it is written the same way a
provider rate edit is — admin-only, no second factor.

Revision ID: ae88bf29885d
Revises: c8e3a1f60b47
"""

import sqlalchemy as sa
from alembic import op

revision = "ae88bf29885d"
down_revision = "c8e3a1f60b47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_markup_overrides",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column(
            "model", sa.String(length=128), nullable=False, server_default=""
        ),
        sa.Column("component", sa.String(length=16), nullable=False),
        sa.Column("markup_bps", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "set_by",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_managed_markup_overrides_lookup",
        "managed_markup_overrides",
        ["provider", "component", "model", "effective_from"],
        unique=False,
    )
    op.create_index(
        "uq_managed_markup_overrides_open",
        "managed_markup_overrides",
        ["provider", "component", "model"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_managed_markup_overrides_open",
        table_name="managed_markup_overrides",
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    op.drop_index(
        "ix_managed_markup_overrides_lookup", table_name="managed_markup_overrides"
    )
    op.drop_table("managed_markup_overrides")
