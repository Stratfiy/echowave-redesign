"""Care: medicine reminders without a phone number

A reminder now has a channel: ``call`` (as before, rings ``phone``) or
``app`` (shown in Decibyl and sent on the person's own notification
channels), so the feature works on a workspace with no number. Existing rows
are calls. ``phone`` becomes nullable for app reminders.

Downgrade deletes app reminders (they have no number to ring) and puts the
column back as it was.

Revision ID: 20261008careapp
Revises: 20261009meetingstasks
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008careapp"
down_revision = "20261009meetingstasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "care_medicines",
        sa.Column(
            "channel", sa.String(8), nullable=False, server_default=sa.text("'call'")
        ),
    )
    op.alter_column(
        "care_medicines", "phone", existing_type=sa.String(20), nullable=True
    )


def downgrade() -> None:
    op.execute("DELETE FROM care_medicines WHERE phone IS NULL")
    op.alter_column(
        "care_medicines", "phone", existing_type=sa.String(20), nullable=False
    )
    op.drop_column("care_medicines", "channel")
