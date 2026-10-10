"""Evolving skills: experience records and skill versions

Additive only: two tables (services/evolve). ``experience_records`` is the
content-minimised ledger of task attempts, corrections and rejected cards,
unique by organisation, run and kind; ``skill_versions`` holds every version
of a workspace's procedure for a skill, with its evidence, evaluation and who
published or rolled it back. Written only while ``evolve_skills`` is on, so
with the flag off both stay empty.

Downgrading drops both tables.

Revision ID: 20261012evolveskills
Revises: 20261011escalations
"""

import sqlalchemy as sa
from alembic import op

revision = "20261012evolveskills"
down_revision = "20261011escalations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experience_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("scope", sa.String(12), nullable=False, server_default="workspace"),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("run_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("skill_slug", sa.String(64), nullable=True),
        sa.Column("skill_version", sa.Integer(), nullable=True),
        sa.Column("task_family", sa.String(96), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("correction", sa.JSON(), nullable=True),
        sa.Column("split", sa.String(8), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_experience_records_org_run_kind",
        "experience_records",
        ["organization_id", "run_key", "kind"],
        unique=True,
    )
    op.create_index(
        "ix_experience_records_org_family",
        "experience_records",
        ["organization_id", "task_family", "split"],
    )

    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evaluation", sa.JSON(), nullable=True),
        sa.Column("cost", sa.JSON(), nullable=False),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("card_event_id", sa.Integer(), nullable=True),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "decided_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "rolled_back_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_skill_versions_org_slug_version",
        "skill_versions",
        ["organization_id", "slug", "version"],
        unique=True,
    )
    op.create_index(
        "ix_skill_versions_org_status",
        "skill_versions",
        ["organization_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_skill_versions_org_status", table_name="skill_versions")
    op.drop_index("uq_skill_versions_org_slug_version", table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_index("ix_experience_records_org_family", table_name="experience_records")
    op.drop_index("uq_experience_records_org_run_kind", table_name="experience_records")
    op.drop_table("experience_records")
