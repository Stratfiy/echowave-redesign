"""Workspace suspension and call-content consent (phase 3, `staff`; STAFF.md)

Additive only:

* ``organizations.staff_suspended_at``, set by the approved
  ``workspace.suspend`` staff command and cleared by ``workspace.unsuspend``.
  Read only while ``staff_console`` is on, so with the flag off it is inert.
* ``staff_content_grants``: a workspace's consent for staff to read call
  transcripts and recordings, per call or for all calls, with an expiry.

Downgrading drops both (any suspension in force and every grant).

Revision ID: 20261009phase3staff
Revises: 20261009mobile
"""

import sqlalchemy as sa
from alembic import op

revision = "20261009phase3staff"
down_revision = "20261009mobile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("staff_suspended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "staff_content_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "granted_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_staff_content_grants_org_run",
        "staff_content_grants",
        ["organization_id", "workflow_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_staff_content_grants_org_run", table_name="staff_content_grants")
    op.drop_table("staff_content_grants")
    op.drop_column("organizations", "staff_suspended_at")
