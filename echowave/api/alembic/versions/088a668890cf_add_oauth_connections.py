"""add oauth connections

Revision ID: 088a668890cf
Revises: a19dc9bf1a4a
Create Date: 2026-07-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "088a668890cf"
down_revision: Union[str, None] = "a19dc9bf1a4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("external_account_email", sa.String(), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_oauth_connections_id"), "oauth_connections", ["id"], unique=False
    )
    op.create_index(
        "idx_oauth_connections_org_provider",
        "oauth_connections",
        ["organization_id", "provider"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_oauth_connections_org_provider", table_name="oauth_connections"
    )
    op.drop_index(op.f("ix_oauth_connections_id"), table_name="oauth_connections")
    op.drop_table("oauth_connections")
