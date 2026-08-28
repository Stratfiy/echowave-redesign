"""Contact lists an inbound number can match a caller against, and who gets through.

Two things a published inbound number needs and had no way to express.

**Who is calling.** Campaign contacts are a CSV in object storage keyed by
``campaigns.source_id`` — fine for reading top to bottom while dialling out,
useless for the inbound question, which is a point lookup while a phone is
ringing. ``contact_lists``/``contacts`` make that a query. ``attributes`` is
open JSON because what an account wants in front of an agent is theirs, and
enumerating those columns would mean a migration per customer.

**Whether they get through.** Five columns on ``telephony_phone_numbers``: the
list to match against, whether an unknown caller is refused, and a per-caller
call limit with the window it is counted over. They live on the number rather
than the workflow because the number is what a stranger dials — one agent may
answer both a published support line and a number given only to existing
customers, and those want different rules.

Every default is the behaviour before this migration: no list, strangers
welcome, no limit. A number that silently changes who it answers on deploy is
the failure mode worth designing out.

Revision ID: c7a91f4b2de8
Revises: d1e7b93a5c40
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c7a91f4b2de8"
down_revision = "d1e7b93a5c40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contact_lists",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_contact_lists_org_name"
        ),
    )

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_list_id",
            sa.Integer(),
            sa.ForeignKey("contact_lists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone_raw", sa.String(length=255), nullable=False),
        sa.Column("phone_normalized", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        # One row per caller per list. Re-importing a CSV updates rather than
        # duplicates, which is what makes a second upload a refresh instead of
        # a mess somebody has to clean up by hand.
        sa.UniqueConstraint(
            "contact_list_id", "phone_normalized", name="uq_contacts_list_phone"
        ),
    )
    op.create_index(
        "ix_contacts_lookup", "contacts", ["contact_list_id", "phone_normalized"]
    )
    op.create_index("ix_contacts_org", "contacts", ["organization_id"])

    op.add_column(
        "telephony_phone_numbers",
        sa.Column(
            "inbound_contact_list_id",
            sa.Integer(),
            sa.ForeignKey("contact_lists.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "telephony_phone_numbers",
        sa.Column(
            "inbound_require_known_caller",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "telephony_phone_numbers",
        # NULL is unlimited, and is the default. A limit nobody asked for is a
        # dropped call from a customer who redialled after a bad line.
        sa.Column("inbound_max_calls_per_caller", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telephony_phone_numbers",
        sa.Column(
            "inbound_call_window_hours",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("24"),
        ),
    )
    op.add_column(
        "telephony_phone_numbers",
        sa.Column(
            "inbound_allow_list",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("telephony_phone_numbers", "inbound_allow_list")
    op.drop_column("telephony_phone_numbers", "inbound_call_window_hours")
    op.drop_column("telephony_phone_numbers", "inbound_max_calls_per_caller")
    op.drop_column("telephony_phone_numbers", "inbound_require_known_caller")
    op.drop_column("telephony_phone_numbers", "inbound_contact_list_id")

    op.drop_index("ix_contacts_org", table_name="contacts")
    op.drop_index("ix_contacts_lookup", table_name="contacts")
    op.drop_table("contacts")
    op.drop_table("contact_lists")
