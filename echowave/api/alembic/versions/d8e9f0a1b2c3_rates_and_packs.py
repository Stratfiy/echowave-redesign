"""Voice rates, overage flag, early-adopter gate (KAN-47, 14 Sept).

* ``managed_bundles.plan_rates`` on the Everyday bundle move to 13 / 12 / 11
  credits a minute on Business / Growth / Scale (₹6.50 / 6.00 / 5.50) and
  the list price to 13.
* ``workflow_runs.overage_applied``: whether a call was priced past the
  plan's credits, so the KPI board can count overage rather than guess it.
* ``organizations.early_adopter_until``: a staff-set date until which the
  account may buy the gated ₹500 pack.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op

revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs", sa.Column("overage_applied", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "organizations",
        sa.Column("early_adopter_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE managed_bundles
            SET list_paise_per_minute = 650,
                plan_rates = '{"business": 650, "growth": 600, "scale": 550}'
            WHERE slug = 'everyday'
            """
        )
    )


def downgrade() -> None:
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
    op.drop_column("organizations", "early_adopter_until")
    op.drop_column("workflow_runs", "overage_applied")
