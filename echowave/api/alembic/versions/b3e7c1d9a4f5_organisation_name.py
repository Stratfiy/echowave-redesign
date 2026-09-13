"""organisations get a name

Revision ID: b3e7c1d9a4f5
Revises: a9d4e1f27c03
Create Date: 2026-09-13

The switcher has been selecting ``organizations.name`` since #200, and the
table never had the column: the model, the migrations and the query
disagreed, so ``GET /organizations/mine`` raised before it reached the
database and every account saw its number instead of its name.

Nullable, because nobody has typed one yet. The route falls back to
"Organization {id}" until an admin renames it.
"""

import sqlalchemy as sa
from alembic import op

revision = "b3e7c1d9a4f5"
down_revision = "a9d4e1f27c03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations", sa.Column("name", sa.String(length=120), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("organizations", "name")
