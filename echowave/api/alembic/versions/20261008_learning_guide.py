"""Learning Guide data (launch stream `learning`)

Six new tables -- learner profiles, goals, skills, lessons, exercises and
attempts -- and nothing changed in an existing one, so a downgrade only
drops what this added. See LEARNING.md.

Revision ID: 20261008learning
Revises: 20261008reach01
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008learning"
down_revision = "20261008reach01"
branch_labels = None
depends_on = None


def _owner() -> list[sa.Column]:
    return [
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "learner_profiles",
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
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("explanation_language", sa.String(length=16), nullable=True),
        sa.Column("learner_kind", sa.String(length=16), nullable=False),
        sa.Column("studying_for", sa.String(length=160), nullable=True),
        sa.Column("adult_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_learner_profile"),
    )
    op.create_table(
        "learning_goals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("goal_uuid", sa.String(length=36), nullable=False, unique=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("studying_for", sa.String(length=160), nullable=True),
        sa.Column("explanation_language", sa.String(length=16), nullable=False),
        sa.Column("material", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("baseline_question", sa.Text(), nullable=True),
        sa.Column("baseline_answer", sa.Text(), nullable=True),
        sa.Column("baseline_level", sa.String(length=16), nullable=True),
        sa.Column("baseline_feedback", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.String(length=36), nullable=True),
        sa.Column("sensitive_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "review_reminders",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_practised_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_learning_goals_owner", "learning_goals", ["organization_id", "user_id"]
    )
    op.create_table(
        "learning_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "goal_id",
            sa.Integer(),
            sa.ForeignKey("learning_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_owner(),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("evaluated_attempts", sa.Integer(), nullable=False),
        sa.Column("passed_attempts", sa.Integer(), nullable=False),
        sa.Column("misses_in_a_row", sa.Integer(), nullable=False),
        sa.Column("interval_step", sa.Integer(), nullable=False),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", sa.String(length=16), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("goal_id", "name", name="uq_learning_skill_name"),
    )
    op.create_index(
        "ix_learning_skills_due", "learning_skills", ["user_id", "next_review_at"]
    )
    op.create_table(
        "learning_lessons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "goal_id",
            sa.Integer(),
            sa.ForeignKey("learning_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_owner(),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "skill_id",
            sa.Integer(),
            sa.ForeignKey("learning_skills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("objective", sa.String(length=300), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("written_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("goal_id", "sequence", name="uq_learning_lesson_seq"),
    )
    op.create_table(
        "learning_exercises",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exercise_uuid", sa.String(length=36), nullable=False, unique=True),
        sa.Column(
            "goal_id",
            sa.Integer(),
            sa.ForeignKey("learning_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lesson_id",
            sa.Integer(),
            sa.ForeignKey("learning_lessons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            sa.Integer(),
            sa.ForeignKey("learning_skills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_owner(),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("rubric", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "learning_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "exercise_id",
            sa.Integer(),
            sa.ForeignKey("learning_exercises.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            sa.Integer(),
            sa.ForeignKey("learning_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_owner(),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("rubric_results", sa.JSON(), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("marked_by", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_learning_attempt_key"
        ),
    )
    op.create_index(
        "ix_learning_attempts_goal", "learning_attempts", ["goal_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_learning_attempts_goal", table_name="learning_attempts")
    op.drop_table("learning_attempts")
    op.drop_table("learning_exercises")
    op.drop_table("learning_lessons")
    op.drop_index("ix_learning_skills_due", table_name="learning_skills")
    op.drop_table("learning_skills")
    op.drop_index("ix_learning_goals_owner", table_name="learning_goals")
    op.drop_table("learning_goals")
    op.drop_table("learner_profiles")
