"""browser_sessions, browser_site_logins, browser_site_rules: Decibyl's
private browser (stream ``browser``).

Revision ID: 202610071500browser
Revises: 202610071400ops
Create Date: 2026-10-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202610071500browser"
down_revision: Union[str, None] = "202610071400ops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _org() -> sa.Column:
    return sa.Column(
        "organization_id",
        sa.Integer(),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def _user() -> sa.Column:
    return sa.Column(
        "user_id",
        sa.Integer(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_uuid", sa.String(36), nullable=False),
        _org(),
        _user(),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("request", sa.Text(), nullable=False, server_default=""),
        sa.Column("sites", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_verbs", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("state_note", sa.String(500), nullable=True),
        sa.Column("limits", postgresql.JSONB(), nullable=False),
        sa.Column("used", postgresql.JSONB(), nullable=False),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column("pending", postgresql.JSONB(), nullable=True),
        sa.Column("keep_login_sites", postgresql.JSONB(), nullable=False),
        sa.Column("receipt", postgresql.JSONB(), nullable=True),
        sa.Column("driver_handle", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_browser_sessions_session_uuid",
        "browser_sessions",
        ["session_uuid"],
        unique=True,
    )
    op.create_index(
        "ix_browser_sessions_organization_id", "browser_sessions", ["organization_id"]
    )
    op.create_index("ix_browser_sessions_user_id", "browser_sessions", ["user_id"])
    op.create_index("ix_browser_sessions_state", "browser_sessions", ["state"])

    op.create_table(
        "browser_site_logins",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        _user(),
        sa.Column("site", sa.String(255), nullable=False),
        sa.Column("cookies_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("cookie_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "user_id", "site", name="uq_browser_login_person_site"
        ),
    )
    op.create_index(
        "ix_browser_site_logins_organization_id",
        "browser_site_logins",
        ["organization_id"],
    )
    op.create_index(
        "ix_browser_site_logins_user_id", "browser_site_logins", ["user_id"]
    )

    op.create_table(
        "browser_site_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site", sa.String(255), nullable=False, unique=True),
        sa.Column("rule", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "set_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("browser_site_rules")
    op.drop_index("ix_browser_site_logins_user_id", table_name="browser_site_logins")
    op.drop_index(
        "ix_browser_site_logins_organization_id", table_name="browser_site_logins"
    )
    op.drop_table("browser_site_logins")
    op.drop_index("ix_browser_sessions_state", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_user_id", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_organization_id", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_session_uuid", table_name="browser_sessions")
    op.drop_table("browser_sessions")
