"""app_interactions: one row per action an agent took in outside software

Revision ID: f1c8d3b45e97
Revises: d4b7e2a91c65
Create Date: 2026-09-12 09:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1c8d3b45e97"
down_revision: Union[str, None] = "d4b7e2a91c65"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_interactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("workflow_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("app", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error", sa.String(length=1024), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        # SET NULL rather than CASCADE: when a run is deleted the fact that the
        # agent sent somebody's invoice is still true, and still billable.
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_interactions_id"), "app_interactions", ["id"])
    op.create_index(
        "ix_app_interactions_org_time",
        "app_interactions",
        ["organization_id", "created_at"],
    )
    op.create_index("ix_app_interactions_run", "app_interactions", ["workflow_run_id"])
    op.create_index(
        "ix_app_interactions_app_status", "app_interactions", ["app", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_app_interactions_app_status", table_name="app_interactions")
    op.drop_index("ix_app_interactions_run", table_name="app_interactions")
    op.drop_index("ix_app_interactions_org_time", table_name="app_interactions")
    op.drop_index(op.f("ix_app_interactions_id"), table_name="app_interactions")
    op.drop_table("app_interactions")
