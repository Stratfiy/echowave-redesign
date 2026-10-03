"""site_projects: the web apps an organisation builds from the Studio chat.

Revision ID: 202610031200studio
Revises: 202610011200admin1
Create Date: 2026-10-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202610031200studio"
down_revision: Union[str, None] = "202610011200admin1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("framework", sa.String(32), nullable=False),
        sa.Column("files", postgresql.JSONB(), nullable=False),
        sa.Column("agent_workflow_ids", postgresql.JSONB(), nullable=False),
        sa.Column("preview_token", sa.String(64), nullable=False),
        sa.Column("build_status", sa.String(16), nullable=False),
        sa.Column("build_log", sa.Text(), nullable=True),
        sa.Column("build_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("build_seconds", sa.Float(), nullable=True),
        sa.Column("dist", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_site_projects_organization_id", "site_projects", ["organization_id"]
    )
    op.create_index(
        "ix_site_projects_preview_token",
        "site_projects",
        ["preview_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_site_projects_preview_token", table_name="site_projects")
    op.drop_index("ix_site_projects_organization_id", table_name="site_projects")
    op.drop_table("site_projects")
