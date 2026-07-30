"""telephony kyc

Adds organization_kyc and kyc_documents. Two-stage verification: our staff
pre-screen the documents, then the carrier — which holds the telecom licence —
performs the verification the DoT directive of June 2025 requires. Only the
carrier's approval unblocks calling; see api/services/kyc/state.py.

NOTE: autogenerate also swept in three unrelated pre-existing drifts, all
removed by hand — an added queued_runs index, a destructive drop of
workflow_definitions.call_disposition_codes, and a change making
ix_api_keys_key_hash NON-unique, which would have permitted duplicate API key
hashes. All three are identical on main; tracked in KNOWN_ISSUES.md #10.

Revision ID: 762afdada0cd
Revises: 1a3f2592ee5a
Create Date: 2026-07-29 21:32:23.284994

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "762afdada0cd"
down_revision: Union[str, None] = "1a3f2592ee5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_kyc",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default="not_started", nullable=False
        ),
        sa.Column("business_type", sa.String(length=16), nullable=True),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("gstin", sa.String(length=20), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("forwarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("carrier", sa.String(length=32), nullable=True),
        sa.Column("carrier_reference", sa.String(length=128), nullable=True),
        sa.Column("carrier_status", sa.String(length=64), nullable=True),
        sa.Column("carrier_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("carrier_rejection_reason", sa.Text(), nullable=True),
        sa.Column("carrier_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id"),
    )
    op.create_index(
        "ix_organization_kyc_status_submitted",
        "organization_kyc",
        ["status", "submitted_at"],
        unique=False,
    )
    op.create_table(
        "kyc_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_kyc_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_kyc_id"], ["organization_kyc.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_kyc_documents_kyc", "kyc_documents", ["organization_kyc_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_kyc_documents_kyc", table_name="kyc_documents")
    op.drop_table("kyc_documents")
    op.drop_index("ix_organization_kyc_status_submitted", table_name="organization_kyc")
    op.drop_table("organization_kyc")
