"""No platform fee on a call; the bundle rate by plan.

KAN-54. Decided 14 Sept 2026 (KAN-47): a call carries no per-minute platform
fee. Its margin is the per-component multiplier on each line, and a managed
call is charged its bundle's flat rate, 12 / 11 / 10 credits a minute on
Business, Growth and Scale.

Three things this migration does, each reversible in spirit though not in
history:

* ``managed_bundles.plan_rates``: ``{plan_code: paise_per_minute}``. The
  Everyday bundle moves from 556 to 600 paise a minute (12 credits) at list,
  with 550 on Growth and 500 on Scale.
* Open account rate rows written by plan authorisation ("Plan … authorised")
  are closed, so those accounts fall through to the new zero default. A
  negotiated rate written by a person (any other note) is left alone: a
  contract is a contract.
* Open platform volume tiers are closed for the same reason; the tier table
  stays for a contract that wants one.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "managed_bundles",
        sa.Column("plan_rates", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.execute(
        sa.text(
            """
            UPDATE managed_bundles
            SET list_paise_per_minute = 600,
                plan_rates = '{"business": 600, "growth": 550, "scale": 500}'
            WHERE slug = 'everyday'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE organization_rate_history
            SET effective_to = NOW(),
                note = COALESCE(note, '') || ' (closed 2026-09-14: no platform fee on calls, KAN-54)'
            WHERE effective_to IS NULL
              AND note LIKE 'Plan % authorised%'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE platform_volume_tiers
            SET effective_to = NOW()
            WHERE effective_to IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("managed_bundles", "plan_rates")
