"""platform provider credentials

Decibyl's own API keys for model providers — what makes an account "managed"
rather than bring-your-own.

Stored encrypted (Fernet, keyed by PLATFORM_CREDENTIAL_SECRET) rather than as
plaintext JSON in organization_configurations. A leak of that table exposes one
tenant's key; a leak of this one exposes inference capacity billed to us for
every customer at once, which is a different blast radius and does not belong in
the same storage.

Keyed by (component, provider), not provider alone: one vendor can serve two
components on separate keys and separate billing accounts.

Revision ID: e2a7c418d5b6
Revises: d18f4c60ba73
Create Date: 2026-07-30 06:02:44.318207

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e2a7c418d5b6'
down_revision: Union[str, None] = 'd18f4c60ba73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_provider_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("component", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("key_last_four", sa.String(length=8), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("set_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["set_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "component", "provider", name="uq_platform_provider_credential"
        ),
    )
    op.create_index(
        op.f("ix_platform_provider_credentials_id"),
        "platform_provider_credentials",
        ["id"],
    )
    op.create_index(
        "ix_platform_provider_credentials_lookup",
        "platform_provider_credentials",
        ["component", "provider"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_provider_credentials_lookup",
        table_name="platform_provider_credentials",
    )
    op.drop_index(
        op.f("ix_platform_provider_credentials_id"),
        table_name="platform_provider_credentials",
    )
    op.drop_table("platform_provider_credentials")
