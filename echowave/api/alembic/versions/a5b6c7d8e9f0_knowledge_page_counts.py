"""A knowledge document records its pages, and how many were scanned.

KAN-57. The plan's knowledge cap is in pages, and pages past it are priced
(a tenth of a credit typed, two credits scanned). Both figures are known
only once a document is processed, so they are columns written by the
worker rather than derived from the file: a sum over a JSON column on every
upload is not a query anyone wants at the gate.

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
"""

import sqlalchemy as sa
from alembic import op

revision = "a5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_base_documents", sa.Column("page_count", sa.Integer(), nullable=True)
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column("scanned_page_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_base_documents", "scanned_page_count")
    op.drop_column("knowledge_base_documents", "page_count")
