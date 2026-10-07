"""Settings shell: the person's own settings, memory history, saved items,
personal data requests and temporary conversations (launch stream `settings`)

Additive only: new nullable columns on ``member_preferences`` and four new
tables. Nothing existing is rewritten, so a downgrade drops exactly what this
added and every earlier screen keeps working.

Revision ID: 20261008settings
Revises: 20261008identity
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008settings"
down_revision = "20261008identity"
branch_labels = None
depends_on = None

_PREFERENCE_COLUMNS = (
    sa.Column("preferred_name", sa.String(length=80), nullable=True),
    sa.Column("explanation_language", sa.String(length=16), nullable=True),
    sa.Column("response_length", sa.String(length=16), nullable=True),
    sa.Column("custom_instructions", sa.Text(), nullable=True),
    sa.Column("memory_enabled", sa.Boolean(), nullable=True),
    sa.Column("speaking_speed", sa.Float(), nullable=True),
    sa.Column("captions", sa.Boolean(), nullable=True),
    sa.Column("auto_detect_language", sa.Boolean(), nullable=True),
    sa.Column("onboarding_absorbed_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    for column in _PREFERENCE_COLUMNS:
        op.add_column("member_preferences", column.copy())

    op.create_table(
        "memory_fact_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_id", sa.Integer(), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("change", sa.String(length=16), nullable=False),
        sa.Column("before_value", sa.Text(), nullable=True),
        sa.Column("after_value", sa.Text(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_memory_fact_revisions_fact",
        "memory_fact_revisions",
        ["organization_id", "fact_id"],
    )

    op.create_table(
        "saved_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "source_event_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("thread_id", sa.String(length=36), nullable=True),
        sa.Column("file_ref", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_saved_items_scope", "saved_items", ["organization_id", "owner_user_id"]
    )

    op.create_table(
        "personal_data_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("stores", sa.JSON(), nullable=False),
        sa.Column("card_event_id", sa.BigInteger(), nullable=True),
        sa.Column("export_payload", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_personal_data_requests_user", "personal_data_requests", ["user_id", "kind"]
    )

    op.create_table(
        "temporary_conversations",
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
        sa.Column("thread_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("temporary_conversations")
    op.drop_index("ix_personal_data_requests_user", table_name="personal_data_requests")
    op.drop_table("personal_data_requests")
    op.drop_index("ix_saved_items_scope", table_name="saved_items")
    op.drop_table("saved_items")
    op.drop_index("ix_memory_fact_revisions_fact", table_name="memory_fact_revisions")
    op.drop_table("memory_fact_revisions")
    for column in reversed(_PREFERENCE_COLUMNS):
        op.drop_column("member_preferences", column.name)
