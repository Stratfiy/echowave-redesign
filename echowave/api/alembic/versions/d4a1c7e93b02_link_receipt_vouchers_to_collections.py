"""Link receipt vouchers to autopay collections

A mandate collection has no ``payments`` row to point at — that table is one
attempt by an account to buy credit, and a bank paying a standing instruction
is not that — so a voucher for one had nothing to hang off and none was issued.
This adds the provider's payment id, which is the same key every other
idempotency guard on this path already uses, with a partial unique index so a
redelivered collection cannot produce a second document.

Revision ID: d4a1c7e93b02
Revises: c80e59fe8b0c
"""

import sqlalchemy as sa
from alembic import op

revision = "d4a1c7e93b02"
down_revision = "c80e59fe8b0c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tax_documents",
        sa.Column("provider_payment_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_receipt_voucher_collection",
        "tax_documents",
        ["provider_payment_id"],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'receipt_voucher' AND provider_payment_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_receipt_voucher_collection", table_name="tax_documents")
    op.drop_column("tax_documents", "provider_payment_id")
