"""the Advanced brain: gpt-5 as a managed LLM tier, on sale in the catalogue

Revision ID: f2a7c9e1b3d4
Revises: d5b9f3a2c6e1
Create Date: 2026-09-14

The chat picker's top rung. A tier customers can choose must be in
``platform_models`` or the catalogue says it is not for sale while the
picker offers it.
"""

import sqlalchemy as sa
from alembic import op

revision = "f2a7c9e1b3d4"
down_revision = "d5b9f3a2c6e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO platform_models
                (component, provider, model, label, enabled, sort_order,
                 created_at, updated_at)
            VALUES ('llm', 'openai', 'gpt-5', 'OpenAI GPT-5', true, 0, NOW(), NOW())
            ON CONFLICT (component, provider, model) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            DELETE FROM platform_models
            WHERE component = 'llm' AND provider = 'openai' AND model = 'gpt-5'
            """
        )
    )
