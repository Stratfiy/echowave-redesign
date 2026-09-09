"""Record whether a platform provider key actually works.

Holding a key and holding a *working* key are different facts, and nothing
recorded the second one. A revoked key stayed ``is_active``, so the managed
tier it backed was still offered, an agent saved against it happily, and the
failure surfaced at dial time — to an inbound caller, who can do nothing about
it and tells nobody.

``last_check_ok`` is nullable on purpose. NULL means never checked and must
never be read as "broken": see services/configuration/credential_validation.py,
where only a confirmed failure withdraws a managed tier.

Revision ID: c4a81f2b6d30
Revises: b3f7c02d5e14
"""

import sqlalchemy as sa
from alembic import op

revision = "c4a81f2b6d30"
down_revision = "b3f7c02d5e14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_provider_credentials",
        sa.Column("last_check_ok", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "platform_provider_credentials",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "platform_provider_credentials",
        sa.Column("last_check_error", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_provider_credentials", "last_check_error")
    op.drop_column("platform_provider_credentials", "last_checked_at")
    op.drop_column("platform_provider_credentials", "last_check_ok")
