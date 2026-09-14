"""every finished call gets its line on the bot's thread

Revision ID: e6c1d4b7a9f2
Revises: c4a8e2d1f7b9
Create Date: 2026-09-14

Calls left runs, recordings and costs, and not one row on the bot's own
timeline: a phone bot with a hundred calls opened on "Nothing yet". New calls
get their row at completion (agent_timeline.record_call_ended); this writes
the row for every call that already finished, so the thread is a history and
not a history from today. Idempotent: a run that already has its row is left
alone. Text chat and channel replies are skipped; they wrote their own rows.
"""

import sqlalchemy as sa
from alembic import op

revision = "e6c1d4b7a9f2"
down_revision = "c4a8e2d1f7b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO agent_events
                (organization_id, workflow_id, workflow_run_id, folder_id, at,
                 kind, actor, summary, payload, is_deliverable, visibility, created_at)
            SELECT
                w.organization_id,
                r.workflow_id,
                r.id,
                w.folder_id,
                COALESCE(r.ended_at, r.created_at),
                'call_ended',
                'agent',
                CASE
                    WHEN r.answered_at IS NOT NULL THEN
                        'Call · ' || (COALESCE(r.billable_seconds, 0) / 60)::text || 'm'
                        || lpad((COALESCE(r.billable_seconds, 0) % 60)::text, 2, '0') || 's'
                    ELSE 'Call not answered'
                END,
                json_build_object(
                    'run_id', r.id,
                    'duration_seconds', COALESCE(r.billable_seconds, 0),
                    'answered', r.answered_at IS NOT NULL,
                    'mode', r.mode,
                    'call_type', r.call_type::text,
                    'has_recording', r.recording_url IS NOT NULL,
                    'backfilled', true
                ),
                false,
                'always',
                NOW()
            FROM workflow_runs r
            JOIN workflows w ON w.id = r.workflow_id
            WHERE r.is_completed
              AND r.mode <> 'textchat'
              AND w.organization_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM agent_events e
                  WHERE e.workflow_run_id = r.id AND e.kind = 'call_ended'
              )
            """
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            DELETE FROM agent_events
            WHERE kind = 'call_ended' AND (payload->>'backfilled') = 'true'
            """
        )
    )
