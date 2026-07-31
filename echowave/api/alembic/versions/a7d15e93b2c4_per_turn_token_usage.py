"""Per-turn token usage, so context growth is visible.

Revision ID: a7d15e93b2c4
Revises: f2b9d47c30ae

A voice agent resends the whole conversation on every turn. Turn 1 sends the
system prompt; turn 20 sends the system prompt plus nineteen exchanges. Prompt
tokens therefore grow linearly with turn index, and their *sum over a call*
grows with the square of its length — so a call twice as long costs closer to
four times as much in language-model spend, not twice.

Nothing in this schema could show that. Token usage was aggregated per call and
per model, which gives a total and no shape: two calls with identical totals,
one of them ten short exchanges and the other three long ones, were
indistinguishable. The shape is where the saving is, because the fixes are
structural — summarise older turns, trim the system prompt, turn on prompt
caching — and none of them is visible as a line item.

Three columns, all nullable, because the LLM does not always report usage and a
turn that reports nothing must stay silent rather than claim zero:

``prompt_tokens``      what was sent — at turn N this *is* the context size
``completion_tokens``  what came back, which stays roughly flat across a call
``cached_tokens``      the part of the prompt served from the provider's cache

That third one is the lever. Prompt caching cuts the cost of resent context by
most of its value, and the cache hit rate is not derivable from anything already
stored — the aggregator has been receiving ``cache_read_input_tokens`` from
pipecat all along and folding it into a call-wide total.
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d15e93b2c4"
down_revision = "f2b9d47c30ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "call_turn_metrics", sa.Column("prompt_tokens", sa.Integer(), nullable=True)
    )
    op.add_column(
        "call_turn_metrics", sa.Column("completion_tokens", sa.Integer(), nullable=True)
    )
    op.add_column(
        "call_turn_metrics", sa.Column("cached_tokens", sa.Integer(), nullable=True)
    )
    # The growth query reads turns in index order within a run, which is the
    # index that already exists; nothing new is needed for it.


def downgrade() -> None:
    op.drop_column("call_turn_metrics", "cached_tokens")
    op.drop_column("call_turn_metrics", "completion_tokens")
    op.drop_column("call_turn_metrics", "prompt_tokens")
