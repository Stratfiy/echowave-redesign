"""dialer connections and imported calls (CR-1)

Revision ID: e3a7c5b9d2f1
Revises: d1f4a8b2c6e9
Create Date: 2026-09-22

A business connects its own Exotel or Tata Smartflo account, and each night
the day's human-handled, recorded calls are imported and transcribed for the
telecaller coach. Additive; nothing reads it while DIALER_IMPORT_ENABLED is
off.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3a7c5b9d2f1"
down_revision: str | None = "d1f4a8b2c6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dialer_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vendor", sa.String(32), nullable=False),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("encrypted_credentials", sa.Text(), nullable=False),
        sa.Column("key_last_four", sa.String(8), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="connected"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_dialer_connections_id", "dialer_connections", ["id"], unique=False
    )
    op.create_index(
        "ix_dialer_connections_organization",
        "dialer_connections",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "imported_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey("dialer_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vendor", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("agent_name", sa.String(128), nullable=True),
        sa.Column("agent_number", sa.String(32), nullable=True),
        sa.Column("customer_last_four", sa.String(8), nullable=True),
        sa.Column("recording_key", sa.String(512), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("transcription_model", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "organization_id",
            "vendor",
            "external_id",
            name="uq_imported_call_per_vendor",
        ),
    )
    op.create_index("ix_imported_calls_id", "imported_calls", ["id"], unique=False)
    op.create_index(
        "ix_imported_calls_org_started",
        "imported_calls",
        ["organization_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_imported_calls_expires", "imported_calls", ["expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_imported_calls_expires", table_name="imported_calls")
    op.drop_index("ix_imported_calls_org_started", table_name="imported_calls")
    op.drop_index("ix_imported_calls_id", table_name="imported_calls")
    op.drop_table("imported_calls")
    op.drop_index("ix_dialer_connections_organization", table_name="dialer_connections")
    op.drop_index("ix_dialer_connections_id", table_name="dialer_connections")
    op.drop_table("dialer_connections")
