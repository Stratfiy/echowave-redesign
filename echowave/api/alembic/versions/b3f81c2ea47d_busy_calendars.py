"""Extra calendars that count as busy

``busy_calendar_ids`` on ``google_calendar_connections``.

An appointment kept on a second calendar was invisible to both the conflict
check and availability, so a slot already taken could be booked again. The fix
is a list of calendars that are *read* when deciding whether a slot is free.
Bookings are still written to ``calendar_id`` alone.

Empty for every existing row, and that default is the whole safety of this
change. "Read every calendar on the account" is right for a solo practitioner
whose own appointments sit on a personal calendar, and wrong for a two-doctor
clinic -- merging both doctors would report the clinic full when only one is
booked, which is the same failure as refusing a free slot. Nothing in a
calendar list distinguishes the two cases, so it is the operator's explicit
choice and an account that makes no choice behaves exactly as it does today.

Revision ID: b3f81c2ea47d
Revises: d7a1c3e58b92
"""

import sqlalchemy as sa
from alembic import op

revision = "b3f81c2ea47d"
down_revision = "d7a1c3e58b92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "google_calendar_connections",
        sa.Column(
            "busy_calendar_ids",
            sa.JSON(),
            nullable=False,
            # An empty list rather than NULL: the read path treats this as a
            # sequence, and a nullable column would put a `or []` at every
            # call site, which is the kind of thing that gets forgotten once.
            server_default=sa.text("'[]'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("google_calendar_connections", "busy_calendar_ids")
