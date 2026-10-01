"""feature_overrides: per-organisation and global feature switches set from the staff console (ADMIN-1).

Revision ID: 202610011200admin1
Revises: 202610010100kan277
Create Date: 2026-10-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202610011200admin1"
down_revision: Union[str, None] = "202610010100kan277"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feature_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("feature", sa.String(64), nullable=False),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "set_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # A plain UNIQUE (feature, organization_id) lets any number of NULL-org
    # rows through, so the global switch gets its own partial index.
    op.create_index(
        "uq_feature_overrides_feature_org",
        "feature_overrides",
        ["feature", "organization_id"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_index(
        "uq_feature_overrides_feature_global",
        "feature_overrides",
        ["feature"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )
    op.create_index(
        "ix_feature_overrides_organization_id",
        "feature_overrides",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_feature_overrides_organization_id", table_name="feature_overrides"
    )
    op.drop_index("uq_feature_overrides_feature_global", table_name="feature_overrides")
    op.drop_index("uq_feature_overrides_feature_org", table_name="feature_overrides")
    op.drop_table("feature_overrides")
