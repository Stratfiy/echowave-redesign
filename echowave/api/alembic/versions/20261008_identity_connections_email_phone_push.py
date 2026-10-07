"""Connections, channels, email identity, phone readiness and push
(launch stream `identity`)

Eleven new tables, nothing changed in an existing one, so a downgrade only
drops what this added. See IDENTITY.md.

Revision ID: 20261008identity
Revises: 202610071500shell
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008identity"
down_revision = "202610071500shell"
branch_labels = None
depends_on = None


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _user(name: str = "user_id", nullable: bool = False, ondelete: str = "CASCADE"):
    return sa.Column(
        name,
        sa.Integer(),
        sa.ForeignKey("users.id", ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "connection_consents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user(),
        sa.Column("toolkit", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("purpose", sa.String(200), nullable=True),
        sa.Column("access", sa.JSON(), nullable=False),
        sa.Column("return_to", sa.String(500), nullable=True),
        sa.Column("connected_account_id", sa.String(128), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        _ts("started_at", nullable=False),
        _ts("ready_at"),
        _ts("last_success_at"),
        _ts("revoked_at"),
        _user("revoked_by", nullable=True, ondelete="SET NULL"),
        _ts("updated_at", nullable=False),
    )
    op.create_index(
        "ix_connection_consents_owner",
        "connection_consents",
        ["organization_id", "user_id", "toolkit"],
    )

    op.create_table(
        "channel_checks",
        sa.Column("channel", sa.String(16), primary_key=True),
        _ts("verified_inbound_at"),
        _ts("delivery_ok_at"),
        _ts("delivery_failed_at"),
        sa.Column("failure_code", sa.String(64), nullable=True),
        _ts("updated_at", nullable=False),
    )

    op.create_table(
        "email_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        _user(),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("alias", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("probe_hash", sa.String(64), nullable=True),
        sa.Column("issue_code", sa.String(64), nullable=True),
        _ts("reserved_at", nullable=False),
        _ts("provisioning_at"),
        _ts("active_at"),
        _ts("suspended_at"),
        _ts("released_at"),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        _ts("updated_at", nullable=False),
    )
    op.create_index(
        "ux_email_identities_alias_live",
        "email_identities",
        ["alias"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "ux_email_identities_user_live",
        "email_identities",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )

    op.create_table(
        "email_identity_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "identity_id",
            sa.Integer(),
            sa.ForeignKey("email_identities.id", ondelete="CASCADE"),
            nullable=True,
        ),
        _user(nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("thread_key", sa.String(255), nullable=False),
        sa.Column("from_address", sa.String(320), nullable=True),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        _ts("received_at", nullable=False),
        sa.UniqueConstraint(
            "recipient", "message_id", name="uq_email_identity_message"
        ),
    )
    op.create_index(
        "ix_email_identity_messages_owner",
        "email_identity_messages",
        ["user_id", "thread_key"],
    )

    op.create_table(
        "email_identity_sends",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "identity_id",
            sa.Integer(),
            sa.ForeignKey("email_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user(),
        sa.Column("card_event_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("message_id", sa.String(255), nullable=False, unique=True),
        sa.Column("to_address", sa.String(320), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("detail_code", sa.String(64), nullable=True),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
    )

    op.create_table(
        "card_interest",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _ts("created_at", nullable=False),
    )

    op.create_table(
        "notification_preferences",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("channels", sa.JSON(), nullable=False),
        sa.Column("topics", sa.JSON(), nullable=False),
        sa.Column("quiet_start", sa.String(5), nullable=True),
        sa.Column("quiet_end", sa.String(5), nullable=True),
        sa.Column("private_previews", sa.Boolean(), nullable=False),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        _ts("updated_at", nullable=False),
    )

    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        _user(),
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(255), nullable=False),
        sa.Column("auth", sa.String(255), nullable=False),
        sa.Column("device_label", sa.String(80), nullable=True),
        _ts("created_at", nullable=False),
        _ts("last_success_at"),
        _ts("last_failure_at"),
        sa.Column("failure_code", sa.String(64), nullable=True),
        _ts("revoked_at"),
    )
    op.create_index("ix_push_subscriptions_user", "push_subscriptions", ["user_id"])

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        _user(),
        sa.Column("topic", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("dedupe_key", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint(
            "user_id", "dedupe_key", "channel", name="uq_notification_delivery"
        ),
    )
    op.create_index(
        "ix_notification_deliveries_day",
        "notification_deliveries",
        ["user_id", "topic", "created_at"],
    )

    op.create_table(
        "delivery_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail_code", sa.String(64), nullable=True),
        _ts("recorded_at", nullable=False),
        sa.UniqueConstraint(
            "provider", "provider_message_id", "status", name="uq_delivery_receipt"
        ),
    )
    op.create_index(
        "ix_delivery_receipts_key", "delivery_receipts", ["idempotency_key"]
    )

    op.create_table(
        "number_readiness",
        sa.Column(
            "phone_number_id",
            sa.Integer(),
            sa.ForeignKey("telephony_phone_numbers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _ts("incoming_call_ok_at"),
        _ts("escalation_ok_at"),
        _user("recorded_by", nullable=True, ondelete="SET NULL"),
        _ts("updated_at", nullable=False),
    )


def downgrade() -> None:
    op.drop_table("number_readiness")
    op.drop_index("ix_delivery_receipts_key", table_name="delivery_receipts")
    op.drop_table("delivery_receipts")
    op.drop_index(
        "ix_notification_deliveries_day", table_name="notification_deliveries"
    )
    op.drop_table("notification_deliveries")
    op.drop_index("ix_push_subscriptions_user", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
    op.drop_table("notification_preferences")
    op.drop_table("card_interest")
    op.drop_table("email_identity_sends")
    op.drop_index(
        "ix_email_identity_messages_owner", table_name="email_identity_messages"
    )
    op.drop_table("email_identity_messages")
    op.drop_index("ux_email_identities_user_live", table_name="email_identities")
    op.drop_index("ux_email_identities_alias_live", table_name="email_identities")
    op.drop_table("email_identities")
    op.drop_table("channel_checks")
    op.drop_index("ix_connection_consents_owner", table_name="connection_consents")
    op.drop_table("connection_consents")
