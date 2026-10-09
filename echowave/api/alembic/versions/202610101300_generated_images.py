"""Generated images: posters and ad creatives (services/images/)

Additive only: one table, ``generated_images``, holding each image a provider
made and each reference image a person attached, with where its bytes are
stored and what its vendor charged for it. Read only while
``image_generation`` is on, so with the flag off it is inert.

Downgrading drops the table (the stored objects stay in the bucket).

Revision ID: 20261010images
Revises: 20261010invitedecisions
"""

import sqlalchemy as sa
from alembic import op

revision = "20261010images"
down_revision = "20261010invitedecisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generated_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("image_uuid", sa.String(40), nullable=False, unique=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default=""),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("key_source", sa.String(16), nullable=False, server_default=""),
        sa.Column("format", sa.String(32), nullable=False, server_default=""),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("storage_backend", sa.String(16), nullable=False),
        sa.Column("request_id", sa.String(40), nullable=True),
        sa.Column("option_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parent_uuid", sa.String(40), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column(
            "vendor_cost_paise", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("charged_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_source", sa.String(16), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_generated_images_id", "generated_images", ["id"], unique=False)
    op.create_index(
        "ix_generated_images_org_created",
        "generated_images",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_generated_images_org_request",
        "generated_images",
        ["organization_id", "request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_generated_images_org_request", table_name="generated_images")
    op.drop_index("ix_generated_images_org_created", table_name="generated_images")
    op.drop_index("ix_generated_images_id", table_name="generated_images")
    op.drop_table("generated_images")
