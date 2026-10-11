"""Personal adaptation: a person's own preferences, and per-conversation
context choices

Additive only: two tables (services/personal). ``personal_preferences`` is
the explicit-preference store -- owner, tenant it was said in, kind, value,
source line, observed and effective time, review time, status and the row it
superseded. ``conversation_context_choices`` is which sources one person
left out of one conversation. Written only while ``evolve_personal`` is on,
so with the flag off both stay empty.

Downgrading drops both tables.

Revision ID: 20261014personalprefs
Revises: 20261012evolveskills
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014personalprefs"
down_revision = "20261012evolveskills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personal_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("topic", sa.String(24), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column(
            "source_event_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_thread_id", sa.String(36), nullable=True),
        sa.Column("source_excerpt", sa.String(300), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Integer(),
            sa.ForeignKey("personal_preferences.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_personal_preferences_owner",
        "personal_preferences",
        ["user_id", "status"],
    )
    op.create_index(
        "uq_personal_preferences_live",
        "personal_preferences",
        ["user_id", "kind", "topic"],
        unique=True,
        postgresql_where=sa.text("status = 'confirmed' AND kind <> 'note'"),
    )
    op.create_table(
        "conversation_context_choices",
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
        sa.Column("thread_key", sa.String(40), nullable=False),
        sa.Column("excluded", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_conversation_context_choices",
        "conversation_context_choices",
        ["organization_id", "user_id", "thread_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_conversation_context_choices", table_name="conversation_context_choices"
    )
    op.drop_table("conversation_context_choices")
    op.drop_index("uq_personal_preferences_live", table_name="personal_preferences")
    op.drop_index("ix_personal_preferences_owner", table_name="personal_preferences")
    op.drop_table("personal_preferences")
