"""Escalation v2: turn index, shadow hits and a QA label on each call's outcome

Additive only, on ``call_escalation_outcomes`` (services/escalation):

* ``caller_turn`` -- the caller turn the escalation was decided on, and
  ``caller_turns`` -- how many the call had, so a reviewer's "it should have
  gone at turn 3" can be compared with when it went (the tolerance-window
  measure of Liu et al., "Time to Transfer").
* ``shadow_escalations`` -- what rules in shadow mode would have done.
* ``qa_label`` (resolved_by_ai | escalated_correctly |
  escalated_unnecessarily | should_have_escalated), ``qa_expected_turn``,
  ``qa_labelled_by`` and ``qa_labelled_at`` -- set later by a reviewer.

Written only while ``escalation_v2`` is on, so with the flag off the new
columns stay empty. Downgrading drops them.

Revision ID: 20261012escmeasure
Revises: 20261011escalations
"""

import sqlalchemy as sa
from alembic import op

revision = "20261012escmeasure"
down_revision = "20261011escalations"
branch_labels = None
depends_on = None

TABLE = "call_escalation_outcomes"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("caller_turn", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("caller_turns", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("shadow_escalations", sa.JSON(), nullable=True))
    op.add_column(TABLE, sa.Column("qa_label", sa.String(32), nullable=True))
    op.add_column(TABLE, sa.Column("qa_expected_turn", sa.Integer(), nullable=True))
    op.add_column(
        TABLE,
        sa.Column(
            "qa_labelled_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        TABLE, sa.Column("qa_labelled_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    for column in (
        "qa_labelled_at",
        "qa_labelled_by",
        "qa_expected_turn",
        "qa_label",
        "shadow_escalations",
        "caller_turns",
        "caller_turn",
    ):
        op.drop_column(TABLE, column)
