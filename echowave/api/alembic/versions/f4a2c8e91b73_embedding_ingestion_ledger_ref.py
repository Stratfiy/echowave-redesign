"""Idempotency index for the embedding-ingestion ledger debit.

Document upload embeds every chunk on our key and, until now, billed nothing
for it -- not even as an ``uncosted`` line, because ingestion has no
``workflow_run_id`` to hang a call receipt off. ``services/billing/
embedding_ingestion.py`` debits the credit ledger directly instead, the same
shape ``rentals.py`` uses for a charge that is not a call.

This is the idempotency guarantee for that debit, not an optimisation: a
document's ingestion embeddings must be paid for at most once, even if the
ARQ job crashes between the vendor call and the ledger write and a retry
picks the document back up. Same pattern as ``uq_credit_ledger_rental_ref``
a few migrations back.

Revision ID: f4a2c8e91b73
Revises: ae88bf29885d
"""

import sqlalchemy as sa
from alembic import op

revision = "f4a2c8e91b73"
down_revision = "ae88bf29885d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_credit_ledger_embedding_ingest_ref",
        "credit_ledger",
        ["organization_id", "ref_type", "ref_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'embedding_ingest' AND ref_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_credit_ledger_embedding_ingest_ref", table_name="credit_ledger")
