"""backup restore rehearsals: proof a dump actually comes back

The backup job proves only the write path — a dump was taken, encrypted,
uploaded, and the object is the right size. None of that proves a single row
comes back, and the ways a backup is worthless while looking healthy are all
invisible from the write side: a dump of a replica that had stopped
replicating, an object encrypted under a secret since rotated, a restore that
fails on an extension the host does not have. Each produces a bucket full of
correctly-sized objects and nothing to restore.

Recorded rather than merely logged, because the second half of that gap is that
nobody could tell whether a restore had *ever* been carried out. A readiness
check reading "last rehearsed 4 days ago" is evidence; a runbook line asking
somebody to do it is not.

Failed attempts are kept and read in preference to nothing — the readiness
check reports the newest attempt rather than the newest success, since a
rehearsal that failed four days ago is the most important fact about these
backups and "last known good" is how a broken restore path stays quiet.

Revision ID: a9d31c6e58b2
Revises: f2c58a9e1b76
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9d31c6e58b2"
down_revision: Union[str, None] = "f2c58a9e1b76"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backup_rehearsals",
        sa.Column("id", sa.Integer(), nullable=False),
        # Which object was restored, so a failure traces to one dump rather
        # than to "the backups".
        sa.Column("backup_key", sa.String(length=512), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # Every fingerprint check, its value in the restored copy, and live's
        # value at the time. Kept whole rather than reduced to a boolean so a
        # later reader can see how far behind a *passing* rehearsal was.
        sa.Column("checks", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_backup_rehearsals_started_at",
        "backup_rehearsals",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_backup_rehearsals_id"), "backup_rehearsals", ["id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_backup_rehearsals_id"), table_name="backup_rehearsals")
    op.drop_index("ix_backup_rehearsals_started_at", table_name="backup_rehearsals")
    op.drop_table("backup_rehearsals")
