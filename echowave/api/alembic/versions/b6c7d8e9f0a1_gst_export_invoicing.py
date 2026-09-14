"""PO number and payment terms on a billing profile; FIRC on a payment.

KAN-80. An enterprise invoice carries the customer's purchase-order number
and the payment terms it was agreed on; both live on the billing profile and
are frozen onto each document at issue like the rest of it. An export
payment records the FIRC/FIRA reference that proves the remittance at
filing.

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
"""

import sqlalchemy as sa
from alembic import op

revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "billing_profiles", sa.Column("po_number", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "billing_profiles",
        sa.Column("payment_terms", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "payments", sa.Column("firc_reference", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("payments", "firc_reference")
    op.drop_column("billing_profiles", "payment_terms")
    op.drop_column("billing_profiles", "po_number")
