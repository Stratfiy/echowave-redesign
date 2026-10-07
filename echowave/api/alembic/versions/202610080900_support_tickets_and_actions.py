"""Support tickets, messages, internal notes, attachments and typed support
actions (launch stream `support`; handoff 33, screens 28 and 32-33)

Five new tables, nothing changed in an existing one, so a downgrade only
drops what this added.

Revision ID: 20261008support
Revises: 20261008meetings
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008support"
down_revision = "20261008meetings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requester_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("severity", sa.String(length=12), nullable=False),
        sa.Column(
            "assignee_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("affected_kind", sa.String(length=8), nullable=True),
        sa.Column("affected_id", sa.Integer(), nullable=True),
        sa.Column("shared", sa.JSON(), nullable=False),
        sa.Column("linked_incident", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "reopened_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "requester_user_id",
            "idempotency_key",
            name="ux_support_tickets_requester_key",
        ),
    )
    op.create_index(
        "ix_support_tickets_org_requester",
        "support_tickets",
        ["organization_id", "requester_user_id"],
    )
    op.create_index(
        "ix_support_tickets_status_created", "support_tickets", ["status", "created_at"]
    )

    op.create_table(
        "support_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ticket_id",
            sa.Integer(),
            sa.ForeignKey("support_tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("author_kind", sa.String(length=12), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("client_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ticket_id", "client_key", name="ux_support_messages_key"),
    )
    op.create_index(
        "ix_support_messages_ticket", "support_messages", ["ticket_id", "created_at"]
    )

    op.create_table(
        "support_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ticket_id",
            sa.Integer(),
            sa.ForeignKey("support_tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("client_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ticket_id", "client_key", name="ux_support_notes_key"),
    )
    op.create_index(
        "ix_support_notes_ticket", "support_notes", ["ticket_id", "created_at"]
    )

    op.create_table(
        "support_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ticket_id",
            sa.Integer(),
            sa.ForeignKey("support_tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "uploaded_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("file_name", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "support_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ticket_id",
            sa.Integer(),
            sa.ForeignKey("support_tickets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("preview", sa.JSON(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column(
            "requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("approved_version", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_note", sa.String(length=500), nullable=True),
        sa.Column("run_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="ux_support_actions_key"),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> requested_by",
            name="ck_support_actions_second_person",
        ),
    )
    op.create_index("ix_support_actions_ticket", "support_actions", ["ticket_id"])
    op.create_index(
        "ix_support_actions_state", "support_actions", ["state", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_support_actions_state", table_name="support_actions")
    op.drop_index("ix_support_actions_ticket", table_name="support_actions")
    op.drop_table("support_actions")
    op.drop_table("support_attachments")
    op.drop_index("ix_support_notes_ticket", table_name="support_notes")
    op.drop_table("support_notes")
    op.drop_index("ix_support_messages_ticket", table_name="support_messages")
    op.drop_table("support_messages")
    op.drop_index("ix_support_tickets_status_created", table_name="support_tickets")
    op.drop_index("ix_support_tickets_org_requester", table_name="support_tickets")
    op.drop_table("support_tickets")
