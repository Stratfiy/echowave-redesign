"""the managed model catalogue

Revision ID: f2b8c30d5a17
Revises: e1f4a7c92b31
Create Date: 2026-08-21

What Decibyl offers on its own keys, stated once rather than inferred from three
places that could disagree.

The seed is the set of models the managed tiers already resolve to, because
those are, by definition, what we are already selling today. It reads them from
``managed_tiers`` at migration time rather than listing them here: the tier map
is overridable by environment, so a literal list would seed a catalogue that did
not match what this deployment actually runs.
"""

import sqlalchemy as sa
from alembic import op

revision = "f2b8c30d5a17"
down_revision = "e1f4a7c92b31"
branch_labels = None
depends_on = None


def _seed_rows() -> list[dict]:
    """The models the managed tiers point at right now, deduplicated."""
    try:
        from api.services.configuration import managed_tiers
    except Exception:  # noqa: BLE001 — a migration must not need the app to import
        return []

    seen: set[tuple[str, str, str]] = set()
    rows: list[dict] = []
    components = (
        ("llm", managed_tiers.LLM_TIERS),
        ("stt", managed_tiers.STT_TIERS),
        ("tts", managed_tiers.TTS_TIERS),
        (managed_tiers.REALTIME_COMPONENT, managed_tiers.REALTIME_TIERS),
        (managed_tiers.EMBEDDINGS_COMPONENT, managed_tiers.EMBEDDINGS_TIERS),
    )
    for component, tiers in components:
        for tier in tiers:
            upstream = managed_tiers.resolve(component, tier)
            if not upstream.model:
                continue
            key = (component, upstream.provider, upstream.model)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "component": component,
                    "provider": upstream.provider,
                    "model": upstream.model,
                    "label": upstream.model,
                }
            )
    return rows


def upgrade() -> None:
    op.create_table(
        "platform_models",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("component", sa.String(length=24), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_platform_models_slot",
        "platform_models",
        ["component", "provider", "model"],
        unique=True,
    )

    rows = _seed_rows()
    if not rows:
        return
    # executemany through the bind: op.execute takes no parameter list, and a
    # loop of single statements would be one round trip per model.
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO platform_models
                (component, provider, model, label, enabled, sort_order,
                 created_at, updated_at)
            VALUES (:component, :provider, :model, :label, true, 0, NOW(), NOW())
            ON CONFLICT (component, provider, model) DO NOTHING
            """
        ),
        rows,
    )


def downgrade() -> None:
    op.drop_index("uq_platform_models_slot", table_name="platform_models")
    op.drop_table("platform_models")
