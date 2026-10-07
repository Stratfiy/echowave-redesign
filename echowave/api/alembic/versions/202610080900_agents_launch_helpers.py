"""The five launch helpers: workspace switches, saved reports, commitments,
research interests and trackers (launch stream `agents`)

Six new tables, nothing changed in an existing one, so a downgrade only
drops what this added.

Revision ID: 20261008agents
Revises: 20261008identity
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008agents"
down_revision = "20261008identity"
branch_labels = None
depends_on = None


def _org() -> sa.Column:
    return sa.Column(
        "organization_id",
        sa.Integer(),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def _owner() -> sa.Column:
    return sa.Column(
        "owner_user_id",
        sa.Integer(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "helper_workspace_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        sa.Column("helper", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "helper", name="uq_helper_workspace_settings"
        ),
    )
    op.create_table(
        "saved_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False, unique=True),
        _org(),
        _owner(),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("conflicts", sa.JSON(), nullable=False),
        sa.Column("inaccessible", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_saved_reports_org_owner",
        "saved_reports",
        ["organization_id", "owner_user_id"],
    )
    op.create_table(
        "commitments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False, unique=True),
        _org(),
        _owner(),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("counterparty", sa.String(length=200), nullable=False),
        sa.Column("contact", sa.String(length=320), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("follow_up_card_id", sa.Integer(), nullable=True),
        sa.Column("approved_card_id", sa.Integer(), nullable=True),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_commitments_org_owner", "commitments", ["organization_id", "owner_user_id"]
    )
    # One tracked commitment per approving card: a card confirmed from two
    # channels still tracks it once.
    op.create_index(
        "ux_commitments_approved_card",
        "commitments",
        ["organization_id", "approved_card_id"],
        unique=True,
        postgresql_where=sa.text("approved_card_id IS NOT NULL"),
    )
    op.create_table(
        "research_interests",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("interests", sa.JSON(), nullable=False),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "trackers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False, unique=True),
        _org(),
        _owner(),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_trackers_org_owner", "trackers", ["organization_id", "owner_user_id"]
    )
    op.create_table(
        "tracker_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tracker_id",
            sa.Integer(),
            sa.ForeignKey("trackers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _org(),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tracker_entries_tracker", "tracker_entries", ["tracker_id"])


def downgrade() -> None:
    op.drop_index("ix_tracker_entries_tracker", table_name="tracker_entries")
    op.drop_table("tracker_entries")
    op.drop_index("ix_trackers_org_owner", table_name="trackers")
    op.drop_table("trackers")
    op.drop_table("research_interests")
    op.drop_index("ux_commitments_approved_card", table_name="commitments")
    op.drop_index("ix_commitments_org_owner", table_name="commitments")
    op.drop_table("commitments")
    op.drop_index("ix_saved_reports_org_owner", table_name="saved_reports")
    op.drop_table("saved_reports")
    op.drop_table("helper_workspace_settings")
