"""Organization invitations: a way for a second person to join an account

Until now there was none. Membership was mirrored from Stack Auth in SaaS mode
and created once at signup otherwise, so a self-hosted organization had exactly
one member and no mechanism to gain another. Every role below Owner was
unreachable in practice — there was never anybody to be a member.

The token is stored hashed, never in the clear, the same way an API key is: a
pending invitation is a bearer credential for a seat in somebody's account, and
a leaked backup of these hands out seats.

The partial unique index is the load-bearing part. Without it, three clicks of
Invite put three valid tokens in one inbox, and revoking the one visible in the
UI leaves two working links behind.
"""

import sqlalchemy as sa
from alembic import op

revision = "e5c9d2f1a648"
down_revision = "d4b8c1e05a37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'member'"),
        ),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("invited_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["accepted_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_organization_invitations_id", "organization_invitations", ["id"]
    )
    op.create_index(
        "ix_organization_invitations_org",
        "organization_invitations",
        ["organization_id"],
    )
    # Unique so resolving a token is one indexed read rather than a scan.
    op.create_index(
        "ix_organization_invitations_token_hash",
        "organization_invitations",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "uq_organization_invitations_open",
        "organization_invitations",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_organization_invitations_open", table_name="organization_invitations"
    )
    op.drop_index(
        "ix_organization_invitations_token_hash",
        table_name="organization_invitations",
    )
    op.drop_index(
        "ix_organization_invitations_org", table_name="organization_invitations"
    )
    op.drop_index(
        "ix_organization_invitations_id", table_name="organization_invitations"
    )
    op.drop_table("organization_invitations")
