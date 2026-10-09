"""Call outcomes and limits: an honest "unknown", and a reserved daily cap

Additive only.

* ``person_call_allowances`` -- one row per person per local day, counting
  the "call me when it's done" calls reserved for them that day across every
  workspace. Grown by one conditional upsert (``used < cap``), so two
  workspaces at once cannot both take the last slot
  (services/call_when_done/allowance.py).
* ``done_calls.allowance_day`` -- the local day whose slot this call holds,
  NULL when it holds none. Released only on a verified non-dispatch.
* ``done_calls.outcome_history`` and ``care_dose_calls.outcome_history`` --
  every change of outcome, appended, so a late report that corrects an
  earlier reading keeps both.

No state column changes: ``unknown`` is a new value in the existing
VARCHAR(16) ``state`` columns.

Downgrading drops the table and the three columns; any row left in
``unknown`` keeps that state string.

Revision ID: 20261010calloutcomes
Revises: 20261010images
"""

import sqlalchemy as sa
from alembic import op

revision = "20261010calloutcomes"
down_revision = "20261010images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "person_call_allowances",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("local_day", sa.Date(), primary_key=True),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("done_calls", sa.Column("allowance_day", sa.Date(), nullable=True))
    op.add_column("done_calls", sa.Column("outcome_history", sa.JSON(), nullable=True))
    op.add_column(
        "care_dose_calls", sa.Column("outcome_history", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("care_dose_calls", "outcome_history")
    op.drop_column("done_calls", "outcome_history")
    op.drop_column("done_calls", "allowance_day")
    op.drop_table("person_call_allowances")
