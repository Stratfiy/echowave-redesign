"""Meeting capture and records (launch stream `meetings`)

Four new tables, nothing changed in an existing one, so a downgrade only
drops what this added. See MEETINGS.md.

Revision ID: 20261008meetings
Revises: 202610071500shell
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008meetings"
down_revision = "202610071500shell"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=32), nullable=False, unique=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("participants", sa.JSON(), nullable=True),
        sa.Column("origin_thread_id", sa.String(length=36), nullable=True),
        sa.Column("consent_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column(
            "captured_ms", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("capture_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capture_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seq", sa.Integer(), nullable=True),
        sa.Column("upload_name", sa.String(length=200), nullable=True),
        sa.Column(
            "reading_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reading_note", sa.Text(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_meetings_owner",
        "meetings",
        ["organization_id", "owner_user_id", "created_at"],
    )

    op.create_table(
        "meeting_segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("audio", sa.LargeBinary(), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("corrected_text", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "has_action_cue",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transcribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("meeting_id", "seq", name="uq_meeting_segments_seq"),
    )

    op.create_table(
        "meeting_breaks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=True),
        sa.Column("at_ms", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meeting_breaks_meeting", "meeting_breaks", ["meeting_id"])

    op.create_table(
        "meeting_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("owner_name", sa.String(length=120), nullable=True),
        sa.Column("due_text", sa.String(length=120), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.String(length=8), nullable=True),
        sa.Column("missing", sa.JSON(), nullable=True),
        sa.Column("segment_seq", sa.Integer(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column(
            "source_found",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "edited", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("card_event_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meeting_items_meeting", "meeting_items", ["meeting_id", "kind"])


def downgrade() -> None:
    op.drop_index("ix_meeting_items_meeting", table_name="meeting_items")
    op.drop_table("meeting_items")
    op.drop_index("ix_meeting_breaks_meeting", table_name="meeting_breaks")
    op.drop_table("meeting_breaks")
    op.drop_table("meeting_segments")
    op.drop_index("ix_meetings_owner", table_name="meetings")
    op.drop_table("meetings")
