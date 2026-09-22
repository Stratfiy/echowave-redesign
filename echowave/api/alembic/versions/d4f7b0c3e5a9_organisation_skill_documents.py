"""Skills an account imported from a repository, body and all (D-1b)

The shipped catalogue is files in the release; ``organisation_skills`` only
records which of them an account keeps. A skill imported from a GitHub link
on the thread has no file in the release, so its body lives here, under the
same slug the shelf and the bots already key on.

Revision ID: d4f7b0c3e5a9
Revises: c3e6a9b2d4f8
"""

import sqlalchemy as sa
from alembic import op

revision = "d4f7b0c3e5a9"
down_revision = "c3e6a9b2d4f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organisation_skill_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("source_repo", sa.String(length=200), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("source_path", sa.String(length=500), nullable=False),
        sa.Column("licence", sa.String(length=64), nullable=False),
        sa.Column("concerns", sa.JSON(), nullable=False),
        sa.Column(
            "reviewed_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_organisation_skill_documents_id", "organisation_skill_documents", ["id"]
    )
    op.create_index(
        "ix_organisation_skill_documents_organization_id",
        "organisation_skill_documents",
        ["organization_id"],
    )
    op.create_index(
        "uq_organisation_skill_documents_org_slug",
        "organisation_skill_documents",
        ["organization_id", "slug"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("organisation_skill_documents")
