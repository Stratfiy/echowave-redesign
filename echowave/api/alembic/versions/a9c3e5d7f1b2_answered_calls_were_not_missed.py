"""calls with talk time were answered, whatever the carrier stamp says

Revision ID: a9c3e5d7f1b2
Revises: f2a7c9e1b3d4
Create Date: 2026-09-14

``answered_at`` is written by the carrier's status webhook, which only an
outbound call has. The backfill in e6c1d4b7a9f2 judged every call on it, so
every inbound call and every web test call -- a clinic's whole day -- was
written as "Call not answered". A call with billable seconds was answered:
somebody was on the line. Rewritten here for the backfilled rows only; rows
written at completion from now on use the same rule in code.
"""

import sqlalchemy as sa
from alembic import op

revision = "a9c3e5d7f1b2"
down_revision = "f2a7c9e1b3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE agent_events e
            SET summary = 'Call · ' || (r.billable_seconds / 60)::text || 'm'
                          || lpad((r.billable_seconds % 60)::text, 2, '0') || 's',
                payload = e.payload::jsonb || jsonb_build_object(
                    'answered', true,
                    'duration_seconds', r.billable_seconds
                )
            FROM workflow_runs r
            WHERE e.workflow_run_id = r.id
              AND e.kind = 'call_ended'
              AND (e.payload::jsonb ->> 'answered') = 'false'
              AND COALESCE(r.billable_seconds, 0) > 0
            """
        )
    )


def downgrade() -> None:
    # The original judgement cannot be told apart from a genuinely unanswered
    # call once rewritten; the rows stay as they are.
    pass
