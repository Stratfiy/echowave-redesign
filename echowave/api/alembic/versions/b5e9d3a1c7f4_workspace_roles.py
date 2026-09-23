"""workspace roles: a customised agent saved, hired again, shared (MP-2, MP-3)

Revision ID: b5e9d3a1c7f4
Revises: a4d8f1c7e2b5
Create Date: 2026-09-23

Additive; nothing reads it while WORKSPACE_ROLES_ENABLED is off.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5e9d3a1c7f4"
down_revision: Union[str, None] = "a4d8f1c7e2b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("configurations", sa.JSON(), nullable=True),
        sa.Column("template_id", sa.String(128), nullable=True),
        sa.Column(
            "source_workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_role_id",
            sa.Integer(),
            sa.ForeignKey("workspace_roles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("needs", sa.JSON(), nullable=True),
        sa.Column("share_token_hash", sa.String(64), nullable=True),
        sa.Column("shared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint("share_token_hash"),
    )
    op.create_index("ix_workspace_roles_id", "workspace_roles", ["id"])
    op.create_index(
        "ix_workspace_roles_organization", "workspace_roles", ["organization_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_roles_organization", table_name="workspace_roles")
    op.drop_index("ix_workspace_roles_id", table_name="workspace_roles")
    op.drop_table("workspace_roles")
