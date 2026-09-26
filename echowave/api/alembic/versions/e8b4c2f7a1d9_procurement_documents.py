"""procurement documents: the register and its gapless series (Step 1)

Revision ID: e8b4c2f7a1d9
Revises: d7a3b9e1f5c2
Create Date: 2026-09-26

The purchase orders, RFQs, work orders, comparative statements and award
letters an agent drafts, and the per-workspace series they are numbered
from. Additive; nothing reads either table while
PROCUREMENT_DOCS_2026_09_ENABLED is off.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8b4c2f7a1d9"
down_revision: Union[str, None] = "d7a3b9e1f5c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "procurement_series",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("fy", sa.String(5), nullable=False),
        sa.Column("prefix", sa.String(24), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "kind", "fy", "prefix", name="uq_procurement_series"
        ),
    )
    op.create_index("ix_procurement_series_id", "procurement_series", ["id"])

    op.create_table(
        "procurement_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("number", sa.String(64), nullable=False),
        sa.Column("series", sa.String(40), nullable=False),
        sa.Column("counterparty_name", sa.String(255), nullable=True),
        sa.Column("counterparty_gstin", sa.String(15), nullable=True),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("docx_key", sa.String(512), nullable=True),
        sa.Column("pdf_key", sa.String(512), nullable=True),
        sa.Column("xlsx_key", sa.String(512), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "series",
            "number",
            name="uq_procurement_document_number",
        ),
    )
    op.create_index("ix_procurement_documents_id", "procurement_documents", ["id"])
    op.create_index(
        "ix_procurement_documents_organization_id",
        "procurement_documents",
        ["organization_id"],
    )
    op.create_index(
        "ix_procurement_documents_status", "procurement_documents", ["status"]
    )
    op.create_index(
        "ix_procurement_documents_org_kind_status",
        "procurement_documents",
        ["organization_id", "kind", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_procurement_documents_org_kind_status", table_name="procurement_documents"
    )
    op.drop_index("ix_procurement_documents_status", table_name="procurement_documents")
    op.drop_index(
        "ix_procurement_documents_organization_id", table_name="procurement_documents"
    )
    op.drop_index("ix_procurement_documents_id", table_name="procurement_documents")
    op.drop_table("procurement_documents")
    op.drop_index("ix_procurement_series_id", table_name="procurement_series")
    op.drop_table("procurement_series")
