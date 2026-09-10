"""One price a minute per bundle, with volume tiers.

Parented on 9b2e4f7c1d38 to keep this branch on a single head — see
api/tests/test_migration_heads.py.

Revision ID: c5d7e9a1b3f2
Revises: 9b2e4f7c1d38
"""

import sqlalchemy as sa
from alembic import op

revision = "c5d7e9a1b3f2"
down_revision = "9b2e4f7c1d38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "managed_bundles",
        sa.Column("list_paise_per_minute", sa.Integer(), nullable=True),
    )
    op.add_column(
        "managed_bundles",
        sa.Column(
            "volume_tiers",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    # The starting price: Rs 5.56 a minute on Everyday, everything included.
    op.execute(
        "UPDATE managed_bundles SET list_paise_per_minute = 556 WHERE slug = 'everyday'"
    )


def downgrade() -> None:
    op.drop_column("managed_bundles", "volume_tiers")
    op.drop_column("managed_bundles", "list_paise_per_minute")
