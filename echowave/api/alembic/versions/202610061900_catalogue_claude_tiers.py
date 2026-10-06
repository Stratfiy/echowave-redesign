"""Offer the Claude models the brain tiers now point at

Everyday, Smart and Deep moved from OpenAI to Claude (Haiku 4.5, Sonnet 5.5,
Opus 5.5). ``model_catalogue`` sells a model only when it is listed here, so a
tier customers are already on has to be listed or the picker cannot show it.
Each has a rate row in ``default_rates``. Idempotent for the same reason as
``a3f7c21e9b04``: a deployment may have added them by hand.

Revision ID: 202610061900claude
Revises: 202610051800avatar
"""

import sqlalchemy as sa
from alembic import op

revision = "202610061900claude"
down_revision = "202610051800avatar"
branch_labels = None
depends_on = None


_ROWS = (
    ("llm", "anthropic", "claude-haiku-4-5", "Claude Haiku 4.5"),
    ("llm", "anthropic", "claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("llm", "anthropic", "claude-opus-5-5", "Claude Opus 5.5"),
)


def upgrade() -> None:
    models = sa.table(
        "platform_models",
        sa.column("component", sa.String),
        sa.column("provider", sa.String),
        sa.column("model", sa.String),
        sa.column("label", sa.String),
    )
    conn = op.get_bind()
    for component, provider, model, label in _ROWS:
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM platform_models "
                "WHERE component = :c AND provider = :p AND model = :m"
            ),
            {"c": component, "p": provider, "m": model},
        ).first()
        if exists:
            continue
        op.bulk_insert(
            models,
            [
                {
                    "component": component,
                    "provider": provider,
                    "model": model,
                    "label": label,
                }
            ],
        )


def downgrade() -> None:
    conn = op.get_bind()
    for component, provider, model, _label in _ROWS:
        conn.execute(
            sa.text(
                "DELETE FROM platform_models "
                "WHERE component = :c AND provider = :p AND model = :m"
            ),
            {"c": component, "p": provider, "m": model},
        )
