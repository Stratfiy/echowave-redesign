"""who can see which agent: workflows.visibility (KAN-158)

Revision ID: a7d2e9c4b1f6
Revises: f3c9d1a7b2e4
Create Date: 2026-09-28

'everyone' (the default, and every existing row) or 'admins'. A member of
the workspace does not see an admins-only agent anywhere a person is
served: the list, the editor, a channel's roster, the board's names,
Decibyl's reading of the workspace. Runtime paths (a call, a routine, a
trigger) are not people and are not filtered.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7d2e9c4b1f6"
down_revision: Union[str, None] = "f3c9d1a7b2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="everyone",
        ),
    )


def downgrade() -> None:
    op.drop_column("workflows", "visibility")
