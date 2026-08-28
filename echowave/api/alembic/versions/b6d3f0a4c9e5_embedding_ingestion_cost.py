"""Cost side of the embedding-ingestion debit, alongside the ledger charge.

``credit_ledger`` (previous migration) records what a document's ingestion
embeddings were charged. It has no column for what the vendor charged *us* —
the same split ``call_cost_items`` keeps for every call, via
``cost_paise``/``provider_cost_paise``. Without it, "meter everything" only
ever recorded half the pair: the money moved, but the number margin analysis
exists to compare — cost against charge — had nowhere to live.

Revision ID: b6d3f0a4c9e5
Revises: f4a2c8e91b73
"""

import sqlalchemy as sa
from alembic import op

revision = "b6d3f0a4c9e5"
down_revision = "f4a2c8e91b73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_ingestion_costs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("knowledge_base_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "vendor_cost_paise", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("charged_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_embedding_ingestion_costs_org_created",
        "embedding_ingestion_costs",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_embedding_ingestion_costs_document",
        "embedding_ingestion_costs",
        ["document_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_embedding_ingestion_costs_document",
        table_name="embedding_ingestion_costs",
    )
    op.drop_index(
        "ix_embedding_ingestion_costs_org_created",
        table_name="embedding_ingestion_costs",
    )
    op.drop_table("embedding_ingestion_costs")
