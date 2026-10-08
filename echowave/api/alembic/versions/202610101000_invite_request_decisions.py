"""Invite requests: a name, and who approved or rejected them

The waitlist already holds one row per address asking for access. Approving
or rejecting from the mailed Approve / Reject links (services/auth/
invite_requests.py) needs to record who decided, when, and which code was
minted, and the onboarding mail greets the person by name.

Additive only: five nullable columns on ``waitlist_requests``. ``status``
keeps its values (``waitlisted`` / ``invited``) and gains ``rejected``.
Downgrade drops the columns; rejected rows go back to ``waitlisted``.

Revision ID: 20261010invitedecisions
Revises: 20261009phase3staff
"""

import sqlalchemy as sa
from alembic import op

revision = "20261010invitedecisions"
down_revision = "20261009phase3staff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("waitlist_requests", sa.Column("name", sa.String(120), nullable=True))
    op.add_column(
        "waitlist_requests",
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "waitlist_requests",
        sa.Column(
            "decided_by_user_id",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_waitlist_requests_decided_by_user_id",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "waitlist_requests",
        sa.Column("decided_by_email", sa.String(320), nullable=True),
    )
    op.add_column(
        "waitlist_requests",
        sa.Column(
            "invite_id",
            sa.Integer(),
            sa.ForeignKey(
                "signup_invites.id",
                ondelete="SET NULL",
                name="fk_waitlist_requests_invite_id",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE waitlist_requests SET status = 'waitlisted' WHERE status = 'rejected'"
    )
    op.drop_constraint(
        "fk_waitlist_requests_invite_id", "waitlist_requests", type_="foreignkey"
    )
    op.drop_column("waitlist_requests", "invite_id")
    op.drop_column("waitlist_requests", "decided_by_email")
    op.drop_constraint(
        "fk_waitlist_requests_decided_by_user_id",
        "waitlist_requests",
        type_="foreignkey",
    )
    op.drop_column("waitlist_requests", "decided_by_user_id")
    op.drop_column("waitlist_requests", "decided_at")
    op.drop_column("waitlist_requests", "name")
