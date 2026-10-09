"""Folders on the Files page, and a "changed since" index for syncing

Additive only:

* ``file_folders``: nestable, organisation-scoped folders that organise
  files. Named so because ``folders`` already means a *channel*; these never
  touch channels and never change who reads a file.
* ``knowledge_base_documents.file_folder_id``: where a file sits, nullable
  (no folder is the top level). ``ON DELETE SET NULL`` is a backstop only:
  folders are soft-deleted, and the service moves or removes their files
  first.
* ``(organization_id, updated_at)`` indexes on both tables, for the listing a
  desktop or mobile client syncs from.
* ``ix_kb_chunks_fts``: a full-text index over each passage, so Files can be
  searched by their words as well as by meaning.

Downgrading drops the column, the indexes and the table; files keep
everything else and simply sit at the top level again.

Revision ID: 20261010filefolders
Revises: 20261010callwhendone
"""

import sqlalchemy as sa
from alembic import op

revision = "20261010filefolders"
down_revision = "20261010callwhendone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "file_folders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("folder_uuid", sa.String(36), nullable=False, unique=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("file_folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_file_folders_id", "file_folders", ["id"])
    op.create_index(
        "ix_file_folders_org_parent", "file_folders", ["organization_id", "parent_id"]
    )
    op.create_index(
        "ix_file_folders_org_updated_at",
        "file_folders",
        ["organization_id", "updated_at"],
    )

    op.add_column(
        "knowledge_base_documents",
        sa.Column(
            "file_folder_id",
            sa.Integer(),
            sa.ForeignKey("file_folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_kb_documents_file_folder_id",
        "knowledge_base_documents",
        ["file_folder_id"],
    )
    op.create_index(
        "ix_kb_documents_org_updated_at",
        "knowledge_base_documents",
        ["organization_id", "updated_at"],
    )
    # Word search over passages, for what a vector misses (a SKU, an invoice
    # number, a cell under its header) and for files read without
    # embeddings. The expression must match the one the search query uses
    # (KnowledgeBaseClient.keyword_search_chunks) or the index is not used.
    op.execute(
        "CREATE INDEX ix_kb_chunks_fts ON knowledge_base_chunks USING gin "
        "(to_tsvector('simple', coalesce(contextualized_text, chunk_text)))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_fts")
    op.drop_index(
        "ix_kb_documents_org_updated_at", table_name="knowledge_base_documents"
    )
    op.drop_index(
        "ix_kb_documents_file_folder_id", table_name="knowledge_base_documents"
    )
    op.drop_column("knowledge_base_documents", "file_folder_id")
    op.drop_index("ix_file_folders_org_updated_at", table_name="file_folders")
    op.drop_index("ix_file_folders_org_parent", table_name="file_folders")
    op.drop_index("ix_file_folders_id", table_name="file_folders")
    op.drop_table("file_folders")
