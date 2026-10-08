"""Auto becomes the brain of every workspace still on the signup default

Auto routes each piece of work to Claude Haiku, Sonnet or Opus by what it is
(services/routing/brain.py). A workspace whose stored brain is the "default"
tier never chose it: that is the value signup wrote. Those move to Auto. A
workspace that pinned an exact model (slots.llm), runs speech-to-speech, or
is on its own keys keeps exactly what it has.

Downgrade puts "auto" back to "default", which is what Auto resolves to
wherever nothing routes, so no call changes model either way.

Revision ID: 202610071200auto
Revises: 202610061900claude
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "202610071200auto"
down_revision = "202610061900claude"
branch_labels = None
depends_on = None

_KEY = "MODEL_CONFIGURATION_V2"


def _rows(conn):
    return conn.execute(
        sa.text("SELECT id, value FROM organization_configurations WHERE key = :k"),
        {"k": _KEY},
    ).fetchall()


def _swap(src: str, dst: str) -> None:
    conn = op.get_bind()
    for row_id, value in _rows(conn):
        config = value if isinstance(value, dict) else json.loads(value or "{}")
        managed = config.get("decibyl") if config.get("mode") == "decibyl" else None
        if not isinstance(managed, dict):
            continue
        if (managed.get("slots") or {}).get("llm") or (
            managed.get("realtime_tier") or ""
        ).strip():
            continue
        if (managed.get("llm_tier") or "default") != src:
            continue
        managed["llm_tier"] = dst
        conn.execute(
            sa.text("UPDATE organization_configurations SET value = :v WHERE id = :id"),
            {"v": json.dumps(config), "id": row_id},
        )


def upgrade() -> None:
    _swap("default", "auto")


def downgrade() -> None:
    _swap("auto", "default")
