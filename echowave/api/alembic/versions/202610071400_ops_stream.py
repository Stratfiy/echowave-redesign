"""Stream ops: commands, credential rotations and evidence

Three new tables (see api/db/ops_models.py), nothing existing touched; ops
analytics events go through the controls stream's analytics_outbox. Every
reader sits behind a flag that is off by default, so the upgrade changes no
behaviour; the downgrade drops the three tables.

Revision ID: 202610071400ops
Revises: 202610071500shell
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202610071400ops"
down_revision = "202610071500shell"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ops_commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("command", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column(
            "target",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column(
            "requested_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requested_role", sa.String(32), nullable=True),
        sa.Column(
            "approval_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "approved_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preview", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ops_commands_command", "ops_commands", ["command"])
    op.create_index("ix_ops_commands_state", "ops_commands", ["state"])
    op.create_index("ix_ops_commands_created", "ops_commands", ["created_at"])

    op.create_table(
        "platform_credential_rotations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("component", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("staged_encrypted_key", sa.Text(), nullable=True),
        sa.Column("staged_last_four", sa.String(8), nullable=False),
        sa.Column("previous_encrypted_key", sa.Text(), nullable=True),
        sa.Column("previous_last_four", sa.String(8), nullable=True),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column(
            "staged_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("staged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumers_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_platform_credential_rotations_state",
        "platform_credential_rotations",
        ["state"],
    )
    op.create_index(
        "ix_credential_rotations_slot",
        "platform_credential_rotations",
        ["component", "provider"],
    )

    op.create_table(
        "ops_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column(
            "recorded_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ops_evidence_kind", "ops_evidence", ["kind"])
    op.create_index(
        "ix_ops_evidence_kind_time", "ops_evidence", ["kind", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_ops_evidence_kind_time", table_name="ops_evidence")
    op.drop_index("ix_ops_evidence_kind", table_name="ops_evidence")
    op.drop_table("ops_evidence")
    op.drop_index(
        "ix_credential_rotations_slot", table_name="platform_credential_rotations"
    )
    op.drop_index(
        "ix_platform_credential_rotations_state",
        table_name="platform_credential_rotations",
    )
    op.drop_table("platform_credential_rotations")
    op.drop_index("ix_ops_commands_created", table_name="ops_commands")
    op.drop_index("ix_ops_commands_state", table_name="ops_commands")
    op.drop_index("ix_ops_commands_command", table_name="ops_commands")
    op.drop_table("ops_commands")
