"""add internal_billing to organizations

Revision ID: b8e4d1f60a92
Revises: a3f7c21e9d40
Create Date: 2026-09-10 21:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e4d1f60a92"
down_revision: Union[str, None] = "a3f7c21e9d40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Not nullable with a false default: "is this account ours" has an answer
    # for every row, and the answer for an existing one is no. A nullable
    # column would leave every account in a third state that the billing paths
    # would each have to decide how to read.
    op.add_column(
        "organizations",
        sa.Column(
            "internal_billing",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "internal_billing")
