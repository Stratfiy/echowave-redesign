"""Promo codes (KAN-134): the codes, their redemptions, and where a code
sits on a payment or a plan mandate.

Revision ID: b1c2d3e4f5a6
Revises: f0a1b2c3d4e5
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column(
            "applies_to", sa.String(48), nullable=False, server_default=sa.text("'any'")
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column(
            "max_per_account", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "first_payment_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"], unique=True)
    op.create_table(
        "promo_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "promo_code_id",
            sa.Integer(),
            sa.ForeignKey("promo_codes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("payments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "mandate_id",
            sa.Integer(),
            sa.ForeignKey("payment_mandates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "discount_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "bonus_credits", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_promo_redemptions_payment",
        "promo_redemptions",
        ["promo_code_id", "payment_id"],
        unique=True,
        postgresql_where=sa.text("payment_id IS NOT NULL"),
    )
    op.create_index(
        "uq_promo_redemptions_mandate",
        "promo_redemptions",
        ["promo_code_id", "mandate_id"],
        unique=True,
        postgresql_where=sa.text("mandate_id IS NOT NULL"),
    )
    op.create_index(
        "ix_promo_redemptions_org",
        "promo_redemptions",
        ["organization_id", "created_at"],
    )
    op.add_column("payments", sa.Column("promo_code", sa.String(32), nullable=True))
    op.add_column(
        "payments",
        sa.Column(
            "discount_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "payment_mandates", sa.Column("promo_code", sa.String(32), nullable=True)
    )
    # A bonus-credit code pays once per account: the trial row is keyed
    # ``promo:<CODE>`` on the account, and this index is what makes it once.
    op.create_index(
        "uq_credit_ledger_promo_bonus",
        "credit_ledger",
        ["organization_id", "ref_type"],
        unique=True,
        postgresql_where=sa.text("kind = 'trial' AND ref_type LIKE 'promo:%'"),
    )


def downgrade() -> None:
    op.drop_index("uq_credit_ledger_promo_bonus", table_name="credit_ledger")
    op.drop_column("payment_mandates", "promo_code")
    op.drop_column("payments", "discount_minor")
    op.drop_column("payments", "promo_code")
    op.drop_index("ix_promo_redemptions_org", table_name="promo_redemptions")
    op.drop_index("uq_promo_redemptions_mandate", table_name="promo_redemptions")
    op.drop_index("uq_promo_redemptions_payment", table_name="promo_redemptions")
    op.drop_table("promo_redemptions")
    op.drop_index("ix_promo_codes_code", table_name="promo_codes")
    op.drop_table("promo_codes")
