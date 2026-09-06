"""Give an API key an environment

Revision ID: e9b4c72a1f36
Revises: d51b8e2fa7c9
Create Date: 2026-09-06

One key does everything, so handing a developer or an agency API access hands
them the ability to dial your customers. A sandbox key can build against the
product; it can only place calls to numbers the organization has verified.

Backfilled to `production`, and the column is NOT NULL with that server
default. Every key that exists was issued to do real work, and a migration
that quietly demoted them would take an account's integration down at the
moment it deployed.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9b4c72a1f36"
down_revision: Union[str, None] = "d51b8e2fa7c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column(
            "environment",
            sa.String(length=32),
            nullable=False,
            server_default="production",
        ),
    )
    # Kept as a server default rather than dropped after the backfill: a row
    # inserted by anything that has not learned about this column yet is a
    # production key, which is what it was going to be anyway.


def downgrade() -> None:
    op.drop_column("api_keys", "environment")
