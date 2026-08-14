"""why a call failed, as a field rather than a guess

"Why did this call fail" was unanswerable across the fleet. ``queued_runs``
records a refusal reason for the two compliance cases, but a run that started
and went wrong recorded nothing at all — the state machine has initialized,
running and completed, and a call that died is left in whichever of those it
reached.

``failure_reason`` is a short vocabulary (``RunFailureReason``), because the
question is "what is failing across the fleet" and that cannot be asked of a
column holding a thousand distinct sentences. ``failure_detail`` beside it
holds the part that is unique to one call — a provider's own message, a carrier
code — and is never grouped on.

A plain String rather than a Postgres ENUM, deliberately. The vocabulary will
grow, and if every new reason needs DDL then new reasons stop being named and
everything lands in "unknown".

Indexed because the breakdown filters on it on every load of the analytics
screen, and left NULL for the calls that went fine — which is most of them, so
the index stays small.

Revision ID: e8b12d47f0c3
Revises: d5a71b3c9e04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8b12d47f0c3"
down_revision: Union[str, Sequence[str], None] = "d5a71b3c9e04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column("failure_reason", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "workflow_runs", sa.Column("failure_detail", sa.Text(), nullable=True)
    )
    op.create_index(
        "ix_workflow_runs_failure_reason", "workflow_runs", ["failure_reason"]
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_runs_failure_reason", table_name="workflow_runs")
    op.drop_column("workflow_runs", "failure_detail")
    op.drop_column("workflow_runs", "failure_reason")
