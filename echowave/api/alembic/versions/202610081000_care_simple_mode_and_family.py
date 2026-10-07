"""Launch stream care: simple mode, medicine calls, scam checks, tech help
and the family circle

Additive only. One nullable column on ``member_preferences`` (Simple mode;
NULL reads as off) and seven new tables. Nothing is backfilled, and the code
before this revision runs unchanged against the upgraded schema.

Downgrade drops the tables and the column. Medicine reminders, circles and
alerts made meanwhile are lost with them; the action cards that consented to
them stay on the thread as a record.

Revision ID: 20261008care
Revises: 202610071500shell
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008care"
down_revision = "202610071500shell"
branch_labels = None
depends_on = None


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _org() -> sa.Column:
    return sa.Column(
        "organization_id",
        sa.Integer(),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.add_column(
        "member_preferences", sa.Column("simple_mode", sa.Boolean(), nullable=True)
    )

    op.create_table(
        "care_circles",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        sa.Column(
            "person_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(60), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint(
            "organization_id", "person_user_id", name="uq_care_circle_person"
        ),
    )

    op.create_table(
        "care_circle_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "circle_id",
            sa.Integer(),
            sa.ForeignKey("care_circles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _org(),
        sa.Column(
            "member_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("shares", sa.JSON(), nullable=False),
        sa.Column("pending_shares", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("invite_code_hash", sa.String(64), nullable=True),
        _ts("invite_expires_at", nullable=True),
        sa.Column("consent_event_id", sa.Integer(), nullable=True),
        _ts("consented_at", nullable=True),
        _ts("accepted_at", nullable=True),
        _ts("revoked_at", nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index(
        "ix_care_circle_members_circle", "care_circle_members", ["circle_id"]
    )
    op.create_index(
        "ix_care_circle_members_member_user", "care_circle_members", ["member_user_id"]
    )
    op.create_index(
        "uq_care_circle_members_code",
        "care_circle_members",
        ["invite_code_hash"],
        unique=True,
        postgresql_where=sa.text("invite_code_hash IS NOT NULL"),
    )

    op.create_table(
        "care_medicines",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        sa.Column(
            "person_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("times", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("alert_member_ids", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("card_event_id", sa.Integer(), nullable=True),
        sa.Column("approved_version", sa.String(32), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index(
        "ix_care_medicines_org_state", "care_medicines", ["organization_id", "state"]
    )

    op.create_table(
        "care_dose_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        sa.Column(
            "medicine_id",
            sa.Integer(),
            sa.ForeignKey("care_medicines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _ts("due_at"),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("marked_by_user_id", sa.Integer(), nullable=True),
        _ts("outcome_at", nullable=True),
        _ts("alerted_at", nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("medicine_id", "due_at", name="uq_care_dose_due"),
    )
    op.create_index(
        "ix_care_dose_calls_state", "care_dose_calls", ["state", "created_at"]
    )

    op.create_table(
        "care_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        sa.Column(
            "circle_id",
            sa.Integer(),
            sa.ForeignKey("care_circles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_id",
            sa.Integer(),
            sa.ForeignKey("care_circle_members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("share", sa.String(32), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        _ts("created_at"),
        _ts("read_at", nullable=True),
        sa.UniqueConstraint(
            "member_id", "kind", "subject_id", name="uq_care_alert_once"
        ),
    )
    op.create_index("ix_care_alerts_member", "care_alerts", ["member_id", "created_at"])

    op.create_table(
        "care_scam_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        _ts("created_at"),
    )
    op.create_index(
        "ix_care_scam_checks_user",
        "care_scam_checks",
        ["organization_id", "user_id", "created_at"],
    )

    op.create_table(
        "care_help_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("guide", sa.String(64), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("tries", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index(
        "ix_care_help_sessions_user",
        "care_help_sessions",
        ["organization_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_care_help_sessions_user", table_name="care_help_sessions")
    op.drop_table("care_help_sessions")
    op.drop_index("ix_care_scam_checks_user", table_name="care_scam_checks")
    op.drop_table("care_scam_checks")
    op.drop_index("ix_care_alerts_member", table_name="care_alerts")
    op.drop_table("care_alerts")
    op.drop_index("ix_care_dose_calls_state", table_name="care_dose_calls")
    op.drop_table("care_dose_calls")
    op.drop_index("ix_care_medicines_org_state", table_name="care_medicines")
    op.drop_table("care_medicines")
    op.drop_index("uq_care_circle_members_code", table_name="care_circle_members")
    op.drop_index(
        "ix_care_circle_members_member_user", table_name="care_circle_members"
    )
    op.drop_index("ix_care_circle_members_circle", table_name="care_circle_members")
    op.drop_table("care_circle_members")
    op.drop_table("care_circles")
    op.drop_column("member_preferences", "simple_mode")
