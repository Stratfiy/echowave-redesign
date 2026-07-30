"""gst billing profiles and tax documents

GST on what Decibyl sells. Three tables and four columns:

* ``billing_profiles`` — who the invoice is made out to, and the state code that
  decides CGST+SGST versus IGST for every domestic supply.
* ``document_sequences`` — the last serial issued per series per financial year.
  GST requires a consecutive, gapless number, so it is allocated from a locked
  row rather than from a count (races) or a database sequence (allowed to skip).
* ``tax_documents`` — receipt vouchers and tax invoices as issued, with every
  figure and both parties' details frozen at issue.

The four columns on ``payments`` separate the two amounts a top-up now has:
``amount_paise`` is the credit bought and reaches the ledger, ``gross_paise``
is what the card was charged. The credit ledger stays GST-exclusive throughout,
which is what keeps the rate card and cost engine out of this entirely.

Revision ID: b7e42d9c05af
Revises: a5c93f2b71de
Create Date: 2026-07-30 10:22:41.663204

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e42d9c05af"
down_revision: Union[str, None] = "a5c93f2b71de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "billing_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("legal_name", sa.String(length=256), nullable=True),
        sa.Column("gstin", sa.String(length=15), nullable=True),
        sa.Column("address_line1", sa.String(length=256), nullable=True),
        sa.Column("address_line2", sa.String(length=256), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("state_code", sa.String(length=2), nullable=True),
        sa.Column("postal_code", sa.String(length=16), nullable=True),
        sa.Column(
            "country_code", sa.String(length=2), nullable=False, server_default="IN"
        ),
        sa.Column("billing_email", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id"),
    )
    op.create_index(op.f("ix_billing_profiles_id"), "billing_profiles", ["id"])

    op.create_table(
        "document_sequences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("financial_year", sa.String(length=5), nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "financial_year", name="uq_document_sequence"),
    )
    op.create_index(op.f("ix_document_sequences_id"), "document_sequences", ["id"])

    op.create_table(
        "tax_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("financial_year", sa.String(length=5), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("taxable_paise", sa.BigInteger(), nullable=False),
        sa.Column("cgst_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sgst_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("igst_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_paise", sa.BigInteger(), nullable=False),
        sa.Column("supply_type", sa.String(length=16), nullable=False),
        sa.Column("place_of_supply", sa.String(length=8), nullable=True),
        sa.Column("rate_basis_points", sa.Integer(), nullable=False),
        sa.Column("supplier_snapshot", sa.JSON(), nullable=False),
        sa.Column("customer_snapshot", sa.JSON(), nullable=False),
        sa.Column("line_items", sa.JSON(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "kind", "financial_year", "number", name="uq_tax_document_number"
        ),
    )
    op.create_index(op.f("ix_tax_documents_id"), "tax_documents", ["id"])
    op.create_index(
        "ix_tax_documents_org_issued", "tax_documents", ["organization_id", "issued_at"]
    )
    # A retried monthly run must not issue a second invoice for a month already
    # invoiced — a duplicate invoice is a filing correction, not a deletion.
    op.create_index(
        "uq_tax_invoice_period",
        "tax_documents",
        ["organization_id", "kind", "period_start"],
        unique=True,
        postgresql_where=sa.text("kind = 'tax_invoice' AND period_start IS NOT NULL"),
    )
    op.create_index(
        "uq_receipt_voucher_payment",
        "tax_documents",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'receipt_voucher' AND payment_id IS NOT NULL"),
    )

    # Nullable rather than backfilled: a payment taken before GST existed has no
    # split to record, and that is not the same as a split of zero.
    op.add_column("payments", sa.Column("gross_paise", sa.BigInteger(), nullable=True))
    op.add_column("payments", sa.Column("cgst_paise", sa.BigInteger(), nullable=True))
    op.add_column("payments", sa.Column("sgst_paise", sa.BigInteger(), nullable=True))
    op.add_column("payments", sa.Column("igst_paise", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "igst_paise")
    op.drop_column("payments", "sgst_paise")
    op.drop_column("payments", "cgst_paise")
    op.drop_column("payments", "gross_paise")

    op.drop_index("uq_receipt_voucher_payment", table_name="tax_documents")
    op.drop_index("uq_tax_invoice_period", table_name="tax_documents")
    op.drop_index("ix_tax_documents_org_issued", table_name="tax_documents")
    op.drop_index(op.f("ix_tax_documents_id"), table_name="tax_documents")
    op.drop_table("tax_documents")

    op.drop_index(op.f("ix_document_sequences_id"), table_name="document_sequences")
    op.drop_table("document_sequences")

    op.drop_index(op.f("ix_billing_profiles_id"), table_name="billing_profiles")
    op.drop_table("billing_profiles")
