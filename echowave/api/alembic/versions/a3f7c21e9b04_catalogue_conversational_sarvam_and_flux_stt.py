"""Offer the models the tiers now point at

The catalogue is what a customer's picker shows and what a bundle may name --
`model_catalogue` sells a model only when it is here, we hold a key, and it has
a rate row. Two tier changes moved the first of those without the catalogue
following:

* the voice LLM tiers went to ``sarvam-105b-conversations``. Plain
  ``sarvam-105b`` is a reasoning model: it emits chain-of-thought before any
  answer, cost 6,045ms of a 7,554ms turn, and cannot be told not to.
* a second STT tier, ``instant``, points at Deepgram Flux, which emits its own
  turn boundaries and so skips the ~1,172ms endpointing wait entirely.

Resolution never read this table, so calls already run on the new models; what
was missing is the offer. Seeded disabled=false only where a rate row exists,
which both do (default_rates carries sarvam-105b-conversations and
flux-general-multi).

Flux additionally needs a Deepgram platform key. Without one
``managed_resolution`` logs and leaves the section alone, so an agent choosing
the tier keeps what it had rather than failing -- quiet, but not broken.

Revision ID: a3f7c21e9b04
Revises: c7a91f4b2de8
"""

from alembic import op
import sqlalchemy as sa

revision = "a3f7c21e9b04"
down_revision = "c7a91f4b2de8"
branch_labels = None
depends_on = None


_ROWS = (
    ("llm", "sarvam", "sarvam-105b-conversations", "Sarvam 105B Conversations"),
    ("stt", "deepgram", "flux-general-multi", "Deepgram Flux Multilingual"),
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
        # Idempotent: a deployment that seeded these by hand, or re-ran the
        # catalogue seeder, must not collide with the table's uniqueness
        # constraint on (component, provider, model).
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
