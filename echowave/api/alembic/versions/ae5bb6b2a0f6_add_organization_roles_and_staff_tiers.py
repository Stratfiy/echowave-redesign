"""add organization roles and staff tiers

Revision ID: ae5bb6b2a0f6
Revises: 8b2c6a4f1d3e
Create Date: 2026-08-11 07:09:44.055341

Replaces two flat, roleless privilege concepts with a real matrix:

* ``organization_users`` (plain many-to-many, no role) becomes
  ``organization_memberships`` (role: member | admin | owner). Every
  existing row is backfilled as OWNER, not MEMBER: before this migration,
  every member of an organization had identical access, so OWNER is the
  only backfill that doesn't silently take access away from someone on
  upgrade. New memberships going forward default to MEMBER (see
  ``add_user_to_organization`` in api/db/organization_client.py).
* ``users.is_superuser`` (boolean) becomes ``users.staff_role`` (nullable
  string: null | support | superadmin). Existing superusers become
  SUPERADMIN — the top tier, matching the single tier they had before.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ae5bb6b2a0f6"
down_revision: Union[str, None] = "8b2c6a4f1d3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.String(length=16),
            server_default=sa.text("'member'"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "organization_id", name="_organization_membership_uc"
        ),
    )

    # Backfill as OWNER, not the column default of MEMBER: see module docstring.
    op.execute(
        """
        INSERT INTO organization_memberships (user_id, organization_id, role, created_at)
        SELECT user_id, organization_id, 'owner', now()
        FROM organization_users
        """
    )

    op.drop_table("organization_users")

    op.add_column("users", sa.Column("staff_role", sa.String(length=16), nullable=True))
    op.execute("UPDATE users SET staff_role = 'superadmin' WHERE is_superuser IS TRUE")
    op.drop_column("users", "is_superuser")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_superuser", sa.BOOLEAN(), autoincrement=False, nullable=True),
    )
    op.execute("UPDATE users SET is_superuser = (staff_role IS NOT NULL)")
    op.drop_column("users", "staff_role")

    op.create_table(
        "organization_users",
        sa.Column("user_id", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("organization_id", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("organization_users_organization_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("organization_users_user_id_fkey")
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "organization_id", name=op.f("organization_users_pkey")
        ),
    )
    op.execute(
        """
        INSERT INTO organization_users (user_id, organization_id)
        SELECT user_id, organization_id FROM organization_memberships
        """
    )

    op.drop_table("organization_memberships")
