"""The knowledge base becomes something a plan buys.

Every document is embedded at ingestion on *our* model key for a managed
account, and embeddings have no cost component, no rate and no ledger debit
anywhere in this codebase — deliberately, because nobody in the category meters
them. ElevenLabs sells RAG as a per-tier allowance and Vapi folds it into the
plan; being the only platform charging per embedded token would cost more to
build than it could recover.

What was missing is the other half of that model. The ceiling was one
environment variable, identical for every account whether it had ever paid or
not, so an account on no plan at all could upload a corpus, have us embed it,
and be billed nothing — the cost is real and lands on us. A cap that does not
know who is subscribed is not an entitlement, it is a rate limit.

So the allowance moves onto the plan row, beside ``included_numbers``, and
defaults to zero. Zero is the load-bearing value: an account with no plan has
no knowledge base, which is what makes this a feature a subscription buys
rather than a limit everyone shares.

The starter plan is backfilled to 25MB so no existing subscriber loses a
knowledge base they already have on the day this ships.

Revision ID: b4d7e2f1a9c3
Revises: c7f4a1b93e28
"""

import sqlalchemy as sa
from alembic import op

revision = "b4d7e2f1a9c3"
down_revision = "c7f4a1b93e28"
branch_labels = None
depends_on = None

#: 25MB, matching ``STARTER_PLAN_KNOWLEDGE_BASE_BYTES``. Written out rather
#: than imported: a migration reproduces what the column held on the day it
#: ran, and reading a constant that later moves would make replaying this
#: produce a different database from the one it originally built.
_STARTER_BYTES = 25 * 1024 * 1024


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column(
            "knowledge_base_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    # Only the starter plan, and only where an operator has not already set a
    # figure. A blanket update would overwrite a deliberate allowance on any
    # plan created between this being written and it being run.
    op.execute(
        sa.text(
            "UPDATE subscription_plans SET knowledge_base_bytes = :bytes "
            "WHERE code = 'starter' AND knowledge_base_bytes = 0"
        ).bindparams(bytes=_STARTER_BYTES)
    )


def downgrade() -> None:
    op.drop_column("subscription_plans", "knowledge_base_bytes")
