"""retention policies, access log and erasure requests

The technical half of DPDP and GDPR. Three tables, each backing an obligation
that no document can satisfy on its own:

* ``data_retention_policies`` — per-account windows for how long recordings and
  transcripts are kept. Storage limitation (DPDP s8(7), GDPR Art 5(1)(e)) only
  exists if something deletes things on a schedule.
* ``data_access_log`` — who reached which recording, and when. Needed to answer
  a data principal asking who listened to their call (DPDP s11) and to say what
  was reached during a breach investigation (GDPR Art 33, 72 hours).
* ``erasure_requests`` — what was asked, when, and what was actually deleted.
  Both statutes set a deadline for responding, so a request handled but not
  recorded is indistinguishable from one ignored.

Revision ID: e5c81b73f9a2
Revises: d9a06f2c81b4
Create Date: 2026-07-30 14:12:55.038291

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5c81b73f9a2"
down_revision: Union[str, None] = "d9a06f2c81b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_retention_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("recording_retention_days", sa.Integer(), nullable=True),
        sa.Column("transcript_retention_days", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id"),
    )
    op.create_index(
        op.f("ix_data_retention_policies_id"), "data_retention_policies", ["id"]
    )

    op.create_table(
        "data_access_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor_kind", sa.String(length=24), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_data_access_log_id"), "data_access_log", ["id"])
    op.create_index(
        "ix_data_access_org_created",
        "data_access_log",
        ["organization_id", "created_at"],
    )
    op.create_index("ix_data_access_run", "data_access_log", ["workflow_run_id"])

    op.create_table(
        "erasure_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=24), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runs_affected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("objects_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_erasure_requests_id"), "erasure_requests", ["id"])
    op.create_index(
        "ix_erasure_org_requested",
        "erasure_requests",
        ["organization_id", "requested_at"],
    )
    op.create_index("ix_erasure_status", "erasure_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_erasure_status", table_name="erasure_requests")
    op.drop_index("ix_erasure_org_requested", table_name="erasure_requests")
    op.drop_index(op.f("ix_erasure_requests_id"), table_name="erasure_requests")
    op.drop_table("erasure_requests")

    op.drop_index("ix_data_access_run", table_name="data_access_log")
    op.drop_index("ix_data_access_org_created", table_name="data_access_log")
    op.drop_index(op.f("ix_data_access_log_id"), table_name="data_access_log")
    op.drop_table("data_access_log")

    op.drop_index(
        op.f("ix_data_retention_policies_id"), table_name="data_retention_policies"
    )
    op.drop_table("data_retention_policies")
