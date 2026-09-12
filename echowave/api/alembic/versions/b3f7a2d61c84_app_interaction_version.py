"""app_interactions.definition_id: which version of the agent acted

Revision ID: b3f7a2d61c84
Revises: a7e2c95b1d38
Create Date: 2026-09-12 11:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f7a2d61c84"
down_revision: Union[str, None] = "a7e2c95b1d38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_interactions", sa.Column("definition_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_app_interactions_definition",
        "app_interactions",
        "workflow_definitions",
        ["definition_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_app_interactions_definition",
        "app_interactions",
        ["definition_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_app_interactions_definition", table_name="app_interactions")
    op.drop_constraint(
        "fk_app_interactions_definition", "app_interactions", type_="foreignkey"
    )
    op.drop_column("app_interactions", "definition_id")
