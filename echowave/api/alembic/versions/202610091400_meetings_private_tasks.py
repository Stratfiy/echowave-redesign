"""A task private to one person: a meeting's follow-up (MEETINGS.md)

Additive. One nullable column on ``agent_tasks``; every other task stays
NULL, which is what it always was (visible to the workspace). The tasks
meeting follow-ups already made are backfilled to their meeting's owner
(through ``meeting_items.task_id``), so the ones made before this fix stop
showing to colleagues too; and the workspace audit rows for presses on a
meeting's card lose the card's label (the meeting's words), as every new
row does. Downgrading drops the column; the scrubbed words stay gone.

* ``agent_tasks.private_to_user_id`` -- set when the task was made from a
  meeting a person captured. The board, Today and the board tool show such a
  task to that person alone; a colleague gets 404 for it, the way they do for
  the meeting.

Revision ID: 20261009meetingstasks
Revises: 20261009mobile
"""

import sqlalchemy as sa
from alembic import op

revision = "20261009meetingstasks"
down_revision = "20261009mobile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column(
            "private_to_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE agent_tasks AS t
        SET private_to_user_id = m.owner_user_id
        FROM meeting_items AS i
        JOIN meetings AS m ON m.id = i.meeting_id
        WHERE i.task_id = t.id
          AND t.organization_id = m.organization_id
        """
    )
    op.execute(
        """
        UPDATE audit_entries
        SET subject = 'Private card (meeting_follow_up)'
        WHERE subject_kind = 'card'
          AND (after::jsonb) ->> 'action' = 'meeting_follow_up'
        """
    )


def downgrade() -> None:
    op.drop_column("agent_tasks", "private_to_user_id")
