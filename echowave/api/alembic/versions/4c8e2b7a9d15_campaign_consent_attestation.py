"""Who confirmed the people on a campaign's list agreed to be called.

Parented on f3a9d1c7e2b4 to keep this branch on a single head — see
api/tests/test_migration_heads.py.

Revision ID: 4c8e2b7a9d15
Revises: f3a9d1c7e2b4
"""

import sqlalchemy as sa
from alembic import op

revision = "4c8e2b7a9d15"
down_revision = "f3a9d1c7e2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("consent_attested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaigns", sa.Column("consent_attested_by", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_campaigns_consent_attested_by_users",
        "campaigns",
        "users",
        ["consent_attested_by"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_campaigns_consent_attested_by_users", "campaigns", type_="foreignkey"
    )
    op.drop_column("campaigns", "consent_attested_by")
    op.drop_column("campaigns", "consent_attested_at")
