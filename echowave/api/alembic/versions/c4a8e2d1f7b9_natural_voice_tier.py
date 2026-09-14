"""the natural voice tier is in the catalogue

Revision ID: c4a8e2d1f7b9
Revises: b3e7c1d9a4f5
Create Date: 2026-09-14

The catalogue was seeded once, from the tiers that existed on the day it was
created, and a tier added later resolves to a model the catalogue has never
heard of: it is not sellable, so the picker omits it and the tier is a name
with nothing behind it. This adds the row the `natural` tier resolves to.
Idempotent, the same ON CONFLICT the seed uses.
"""

import sqlalchemy as sa
from alembic import op

revision = "c4a8e2d1f7b9"
down_revision = "b3e7c1d9a4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO platform_models
                (component, provider, model, label, enabled, sort_order,
                 created_at, updated_at)
            VALUES ('tts', 'smallest', 'lightning_v3.1_pro',
                    'Smallest Lightning v3.1 Pro', true, 0, NOW(), NOW())
            ON CONFLICT (component, provider, model) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            DELETE FROM platform_models
            WHERE component = 'tts' AND provider = 'smallest'
              AND model = 'lightning_v3.1_pro'
            """
        )
    )
