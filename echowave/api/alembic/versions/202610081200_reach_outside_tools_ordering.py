"""Outside tools and ordering, per person (launch stream `reach`)

Two new tables, nothing changed in an existing one, so a downgrade only
drops what this added.

Revision ID: 20261008reach01
Revises: 20261008care
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008reach01"
down_revision = "20261008care"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reach_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False, unique=True),
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
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("server_url", sa.String(length=2048), nullable=False),
        sa.Column("auth", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("oauth_state_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "tools", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ux_reach_connections_live",
        "reach_connections",
        ["organization_id", "user_id", "kind", "provider"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_reach_connections_owner",
        "reach_connections",
        ["organization_id", "user_id"],
    )
    op.create_index(
        "ix_reach_connections_oauth_state_hash",
        "reach_connections",
        ["oauth_state_hash"],
    )
    op.create_table(
        "reach_order_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False, unique=True),
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
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey("reach_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column(
            "store", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
        sa.Column(
            "items", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
        sa.Column(
            "quote", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
        sa.Column(
            "address", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
        sa.Column(
            "payment", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("card_event_id", sa.BigInteger(), nullable=True),
        sa.Column("provider_cart_id", sa.String(length=200), nullable=True),
        sa.Column("provider_order_id", sa.String(length=200), nullable=True),
        sa.Column(
            "result", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
        sa.Column("quoted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_reach_order_drafts_owner",
        "reach_order_drafts",
        ["organization_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_reach_order_drafts_owner", table_name="reach_order_drafts")
    op.drop_table("reach_order_drafts")
    op.drop_index(
        "ix_reach_connections_oauth_state_hash", table_name="reach_connections"
    )
    op.drop_index("ix_reach_connections_owner", table_name="reach_connections")
    op.drop_index("ux_reach_connections_live", table_name="reach_connections")
    op.drop_table("reach_connections")
