"""Tables for launch stream `learning` (LAUNCH-PLAN.md, phase 2).

Handoff 6 ("Learning Guide data"): learning goal, preferred explanation
language, baseline, lesson objective, attempts, rubric, feedback and next
review date. Handoff 23 and screens 13-14: a resumable lesson and next
exercise, progress from completed practice and demonstrated understanding.

Every row carries ``organization_id`` and ``user_id``: a learning record is
the learner's own. Nothing here is read by organisation alone -- a
colleague, or a workspace admin, sees none of it (services/learning).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py``
imports this module at its end. See ``LEARNING.md``.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class LearnerProfileModel(Base):
    """One person's learner profile in one workspace (or their personal
    space). Asks for little: the language explanations come in, what they
    are studying for if they want to say, and that they are an adult --
    the launch is adults first (handoff 6); a child-focused launch needs its
    own age, consent and safety design."""

    __tablename__ = "learner_profiles"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: A BCP 47 tag from services/member_preferences.LANGUAGES.
    explanation_language = Column(String(16), nullable=True)
    #: ``adult`` | ``student`` | ``course_learner``. All adults; the last two
    #: say a course or exam frames the practice.
    learner_kind = Column(String(16), nullable=False, default="adult")
    #: "Class 12 boards", "CA Foundation", "IELTS" -- the person's words.
    studying_for = Column(String(160), nullable=True)
    #: When the person said they are 18 or older. Required to start.
    adult_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_learner_profile"),
    )


class LearningGoalModel(Base):
    """One thing a person wants to learn, on any subject."""

    __tablename__ = "learning_goals"

    id = Column(Integer, primary_key=True)
    #: The id screens and links use; never the integer.
    goal_uuid = Column(String(36), nullable=False, unique=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(200), nullable=False)
    #: A course or exam this goal serves, in the person's words.
    studying_for = Column(String(160), nullable=True)
    explanation_language = Column(String(16), nullable=False)
    #: Notes the person pasted to learn from. When present, lessons teach
    #: from it and say so ("From your notes"); otherwise they are general
    #: explanation and say that.
    material = Column(Text, nullable=True)
    #: ``baseline`` (waiting for the first answer) | ``active`` | ``archived``.
    status = Column(String(16), nullable=False, default="baseline")
    baseline_question = Column(Text, nullable=True)
    baseline_answer = Column(Text, nullable=True)
    #: ``new`` | ``some`` | ``confident`` -- from the evaluated baseline.
    baseline_level = Column(String(16), nullable=True)
    baseline_feedback = Column(Text, nullable=True)
    #: The lesson names the teacher planned at placement, in order. Lessons
    #: follow it; null for a goal placed before plans existed.
    plan = Column(JSON, nullable=True)
    #: The Decibyl conversation it started in (handoff 23: saved records
    #: reopen in their original conversation). Null is the original thread.
    thread_id = Column(String(36), nullable=True)
    #: When the person agreed to save details that looked sensitive.
    sensitive_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    #: Opt-in review reminders (screen 14). Due reviews always show in Today.
    review_reminders = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    #: The last evaluated practice. Not touched by reading or by chat.
    last_practised_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_learning_goals_owner", "organization_id", "user_id"),)


class LearningSkillModel(Base):
    """One skill inside a goal, and the evidence for it.

    Moved only by an evaluated attempt (``services.learning.core``): reading
    a lesson, chatting or time spent never changes a row here.
    """

    __tablename__ = "learning_skills"

    id = Column(Integer, primary_key=True)
    goal_id = Column(
        Integer, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    name = Column(String(120), nullable=False)
    #: ``not_practised`` | ``practised`` | ``needs_another_attempt``.
    status = Column(String(24), nullable=False, default="not_practised")
    evaluated_attempts = Column(Integer, nullable=False, default=0)
    passed_attempts = Column(Integer, nullable=False, default=0)
    #: Not-passed answers in a row since the last pass: "stuck" at two.
    misses_in_a_row = Column(Integer, nullable=False, default=0)
    #: Index into services.learning.core.REVIEW_INTERVALS.
    interval_step = Column(Integer, nullable=False, default=0)
    next_review_at = Column(DateTime(timezone=True), nullable=True)
    last_outcome = Column(String(16), nullable=True)
    last_evaluated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("goal_id", "name", name="uq_learning_skill_name"),
        Index("ix_learning_skills_due", "user_id", "next_review_at"),
    )


class LearningLessonModel(Base):
    """A short lesson: one objective, a short explanation."""

    __tablename__ = "learning_lessons"

    id = Column(Integer, primary_key=True)
    goal_id = Column(
        Integer, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    sequence = Column(Integer, nullable=False)
    skill_id = Column(
        Integer, ForeignKey("learning_skills.id", ondelete="CASCADE"), nullable=False
    )
    objective = Column(String(300), nullable=False)
    explanation = Column(Text, nullable=False)
    #: ``material`` (taught from the person's notes) | ``general``.
    source_kind = Column(String(16), nullable=False, default="general")
    #: Short excerpts of the material it relied on, when ``material``.
    sources = Column(JSON, nullable=False, default=list)
    #: Who wrote it: ``model:<provider>`` or ``fake``. Never shown in teaching.
    written_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("goal_id", "sequence", name="uq_learning_lesson_seq"),
    )


class LearningExerciseModel(Base):
    """One practice prompt with the rubric it is marked against."""

    __tablename__ = "learning_exercises"

    id = Column(Integer, primary_key=True)
    exercise_uuid = Column(String(36), nullable=False, unique=True)
    goal_id = Column(
        Integer, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    lesson_id = Column(
        Integer, ForeignKey("learning_lessons.id", ondelete="CASCADE"), nullable=False
    )
    skill_id = Column(
        Integer, ForeignKey("learning_skills.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    #: ``practice`` | ``review``.
    kind = Column(String(16), nullable=False, default="practice")
    prompt = Column(Text, nullable=False)
    #: ``[{"criterion": str, "description": str}]`` -- what a good answer
    #: shows. Marking reports against each one.
    rubric = Column(JSON, nullable=False, default=list)
    #: ``open`` | ``passed`` | ``needs_another_attempt``.
    status = Column(String(24), nullable=False, default="open")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class LearningAttemptModel(Base):
    """One submitted answer and how it was marked."""

    __tablename__ = "learning_attempts"

    id = Column(Integer, primary_key=True)
    exercise_id = Column(
        Integer,
        ForeignKey("learning_exercises.id", ondelete="CASCADE"),
        nullable=False,
    )
    goal_id = Column(
        Integer, ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    answer = Column(Text, nullable=False)
    #: ``passed`` | ``partly`` | ``not_yet``.
    outcome = Column(String(16), nullable=False)
    #: ``[{"criterion": str, "met": bool, "note": str}]``.
    rubric_results = Column(JSON, nullable=False, default=list)
    feedback = Column(Text, nullable=False)
    marked_by = Column(String(64), nullable=True)
    #: The client's key for this submission: a retry is the same attempt,
    #: so completing a practice updates the evidence once (screen 14).
    idempotency_key = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_learning_attempt_key"),
        Index("ix_learning_attempts_goal", "goal_id", "created_at"),
    )
