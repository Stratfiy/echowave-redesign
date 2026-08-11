"""Managed number provisioning, carrier compliance and rental billing

Adds three things that did not exist:

* the fields a Plivo compliance application needs (registered address on the
  KYC record, a content hash per document so duplicate uploads are detectable)
* a lifecycle on phone numbers we bought — status, carrier id, release audit —
  so a released number leaves a record instead of a hole
* recurring charges and their billed periods, which is the whole rental billing
  mechanism; before this the platform had no way to charge for anything except
  a completed call

The partial unique indexes are the idempotency guarantees, not optimisations.
``uq_recurring_charge_period`` is what makes the monthly cron safe to run twice,
and ``uq_credit_ledger_rental_ref`` is what makes it safe to crash between
writing the period and debiting the ledger.

Revision ID: c7a1f4e93b28
Revises: 8b2c6a4f1d3e
"""

import sqlalchemy as sa
from alembic import op

revision = "c7a1f4e93b28"
down_revision = "8b2c6a4f1d3e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- KYC: registered address and document hashes -----------------------
    op.add_column(
        "organization_kyc", sa.Column("address_line1", sa.String(255), nullable=True)
    )
    op.add_column(
        "organization_kyc", sa.Column("address_line2", sa.String(255), nullable=True)
    )
    op.add_column("organization_kyc", sa.Column("city", sa.String(128), nullable=True))
    op.add_column(
        "organization_kyc", sa.Column("region", sa.String(128), nullable=True)
    )
    op.add_column(
        "organization_kyc", sa.Column("postal_code", sa.String(16), nullable=True)
    )
    op.add_column(
        "organization_kyc",
        sa.Column(
            "country_iso", sa.String(2), nullable=False, server_default="IN"
        ),
    )
    op.add_column(
        "kyc_documents", sa.Column("content_sha256", sa.String(64), nullable=True)
    )

    # -- phone numbers: lifecycle and carrier identity ---------------------
    op.add_column(
        "telephony_phone_numbers",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column(
        "telephony_phone_numbers",
        sa.Column("carrier_number_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "telephony_phone_numbers",
        sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "telephony_phone_numbers",
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "telephony_phone_numbers",
        sa.Column("released_by", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telephony_phone_numbers", sa.Column("release_reason", sa.Text(), nullable=True)
    )
    op.create_foreign_key(
        "fk_phone_numbers_released_by",
        "telephony_phone_numbers",
        "users",
        ["released_by"],
        ["id"],
        ondelete="SET NULL",
    )

    # -- recurring charges -------------------------------------------------
    op.create_table(
        "recurring_charges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("charge_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(24), nullable=False, server_default="active"
        ),
        sa.Column(
            "cost_paise", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "price_paise", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "current_period_start", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_charge_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "failure_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_failure_reason", sa.Text(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recurring_charges_id", "recurring_charges", ["id"], unique=False
    )
    op.create_index(
        "ix_recurring_charges_org",
        "recurring_charges",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_recurring_charges_due",
        "recurring_charges",
        ["status", "next_charge_at"],
        unique=False,
    )
    # One live charge per resource. Without this a retried provisioning bills
    # the same number twice a month for ever, and the second row looks as
    # legitimate as the first.
    op.create_index(
        "uq_recurring_charges_live_resource",
        "recurring_charges",
        ["charge_type", "resource_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('released', 'cancelled')"),
    )

    op.create_table(
        "recurring_charge_periods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recurring_charge_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("charged_paise", sa.BigInteger(), nullable=False),
        sa.Column(
            "cost_paise", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "prorated", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("charged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["recurring_charge_id"], ["recurring_charges.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # The idempotency guarantee for the monthly cron.
        sa.UniqueConstraint(
            "recurring_charge_id", "period_start", name="uq_recurring_charge_period"
        ),
    )
    op.create_index(
        "ix_recurring_charge_periods_id",
        "recurring_charge_periods",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_recurring_charge_periods_org",
        "recurring_charge_periods",
        ["organization_id", "charged_at"],
        unique=False,
    )

    # A rental period debits the ledger at most once, even if the worker dies
    # between writing the period row and writing the debit.
    op.create_index(
        "uq_credit_ledger_rental_ref",
        "credit_ledger",
        ["organization_id", "ref_type", "ref_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'rental' AND ref_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_credit_ledger_rental_ref", table_name="credit_ledger")
    op.drop_index(
        "ix_recurring_charge_periods_org", table_name="recurring_charge_periods"
    )
    op.drop_index(
        "ix_recurring_charge_periods_id", table_name="recurring_charge_periods"
    )
    op.drop_table("recurring_charge_periods")
    op.drop_index("uq_recurring_charges_live_resource", table_name="recurring_charges")
    op.drop_index("ix_recurring_charges_due", table_name="recurring_charges")
    op.drop_index("ix_recurring_charges_org", table_name="recurring_charges")
    op.drop_index("ix_recurring_charges_id", table_name="recurring_charges")
    op.drop_table("recurring_charges")

    op.drop_constraint(
        "fk_phone_numbers_released_by", "telephony_phone_numbers", type_="foreignkey"
    )
    op.drop_column("telephony_phone_numbers", "release_reason")
    op.drop_column("telephony_phone_numbers", "released_by")
    op.drop_column("telephony_phone_numbers", "released_at")
    op.drop_column("telephony_phone_numbers", "provisioned_at")
    op.drop_column("telephony_phone_numbers", "carrier_number_id")
    op.drop_column("telephony_phone_numbers", "status")

    op.drop_column("kyc_documents", "content_sha256")
    op.drop_column("organization_kyc", "country_iso")
    op.drop_column("organization_kyc", "postal_code")
    op.drop_column("organization_kyc", "region")
    op.drop_column("organization_kyc", "city")
    op.drop_column("organization_kyc", "address_line2")
    op.drop_column("organization_kyc", "address_line1")
