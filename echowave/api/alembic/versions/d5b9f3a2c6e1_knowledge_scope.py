"""knowledge has a scope: the company's, a channel's, a bot's, or the library

Revision ID: d5b9f3a2c6e1
Revises: e6c1d4b7a9f2
Create Date: 2026-09-14

Every document was the organisation's, and no bot read any of it unless a
node named the document by uuid. That is a library: things on a shelf that
somebody has to hand to an agent one at a time. What people asked for is
company knowledge every bot reads, and a file dropped into a channel that the
bots in that channel read.

Existing rows become ``library`` -- exactly what they were -- so no bot starts
reading a document nobody attached to it.
"""

import sqlalchemy as sa
from alembic import op

revision = "d5b9f3a2c6e1"
down_revision = "e6c1d4b7a9f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_base_documents",
        sa.Column(
            "scope", sa.String(length=16), nullable=False, server_default="library"
        ),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column(
            "folder_id",
            sa.Integer(),
            sa.ForeignKey("folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_kb_documents_org_scope",
        "knowledge_base_documents",
        ["organization_id", "scope"],
    )


def downgrade() -> None:
    op.drop_index("ix_kb_documents_org_scope", table_name="knowledge_base_documents")
    op.drop_column("knowledge_base_documents", "workflow_id")
    op.drop_column("knowledge_base_documents", "folder_id")
    op.drop_column("knowledge_base_documents", "scope")
