"""Tables for evolving skills (services/evolve).

Two, each scoped by ``organization_id`` and read only through it:

* ``experience_records`` -- one row per task attempt, correction or rejected
  card: which skill version and tools were used, and what the *persisted*
  records say happened (an app's own reply, the carrier's answer time, a
  person's message). Never a transcript and never the model's own account of
  how it went. Unique by ``(organization_id, run_key, kind)``, so collecting
  the same run twice writes one row. ``split`` is frozen at insert: a row is
  either something a lesson may be learned from (``train``) or something a
  lesson is tested on (``holdout``), and it never changes sides.
* ``skill_versions`` -- every version of a workspace's procedure for one
  skill: the person's own, a remembered draft, or a lesson learned from
  experience. The active version is the newest ``published`` one; rolling
  back marks it ``rolled_back`` and the one before it is active again,
  exactly as it was.

Kept out of ``models.py`` (launch convention); ``models.py`` imports this
module at its end so the tables are on ``Base.metadata``.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ExperienceRecordModel(Base):
    """One thing that happened while a skill was in use, and the proof."""

    __tablename__ = "experience_records"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: Whose task it was, where there is a person behind it.
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: ``workspace`` or ``personal``. A personal record came from one person's
    #: own conversation: only that person's own skills may learn from it, and
    #: nobody else may read it.
    scope = Column(String(12), nullable=False, default="workspace")
    workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    workflow_run_id = Column(
        Integer, ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True
    )
    #: What makes the row unique with ``kind``: ``run:<id>``, ``event:<id>``.
    run_key = Column(String(64), nullable=False)
    #: attempt | correction | rejected_card
    kind = Column(String(24), nullable=False)
    skill_slug = Column(String(64), nullable=True)
    #: The workspace version of the skill in use at the time; null for the
    #: shipped text with no workspace version.
    skill_version = Column(Integer, nullable=True)
    #: What "related" means: the skill's slug, or ``agent:<id>`` for an
    #: attempt by an agent carrying no skill.
    task_family = Column(String(96), nullable=False)
    #: ``[{kind, name, app, status, definition_id}]`` -- names and statuses,
    #: never arguments.
    tool_calls = Column(JSON, nullable=False, default=list)
    #: ``[{source, ref, state}]`` -- each one a pointer to a persisted row.
    evidence = Column(JSON, nullable=False, default=list)
    #: success | failure | unknown, read off ``evidence`` alone.
    outcome = Column(String(16), nullable=False, default="unknown")
    #: ``{category, instruction, ref}`` for a person's correction.
    correction = Column(JSON, nullable=True)
    #: train | holdout, decided once from the row's identity.
    split = Column(String(8), nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index(
            "uq_experience_records_org_run_kind",
            "organization_id",
            "run_key",
            "kind",
            unique=True,
        ),
        Index(
            "ix_experience_records_org_family",
            "organization_id",
            "task_family",
            "split",
        ),
    )


class SkillVersionModel(Base):
    """One version of a workspace's way of doing one skill."""

    __tablename__ = "skill_versions"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    slug = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False)
    #: draft | rejected | offered | published | rolled_back | discarded
    status = Column(String(16), nullable=False, default="draft")
    #: person | learned | remembered
    origin = Column(String(16), nullable=False)
    #: The procedure, in fixed fields only (services/evolve/guard.py).
    content = Column(JSON, nullable=False, default=dict)
    #: The published version this one was proposed against.
    base_version = Column(Integer, nullable=True)
    #: ``[{id, kind, outcome, summary}]`` -- the experience it came from.
    evidence = Column(JSON, nullable=False, default=list)
    #: What the gate found: related and unrelated pass counts against the
    #: base, the deltas, whether it passed and why not.
    evaluation = Column(JSON, nullable=True)
    #: ``{model_calls, tokens}`` spent proposing and testing it.
    cost = Column(JSON, nullable=False, default=dict)
    #: Where the card goes: the agent's thread, or Decibyl's.
    workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    thread_id = Column(String(64), nullable=True)
    card_event_id = Column(Integer, nullable=True)
    author_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Set while a remembered draft is one person's own: nobody else sees it
    #: until it is published.
    owner_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    decided_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    rolled_back_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rolled_back_at = Column(DateTime(timezone=True), nullable=True)
    #: Why it was rejected, discarded or rolled back, in a sentence.
    reason = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index(
            "uq_skill_versions_org_slug_version",
            "organization_id",
            "slug",
            "version",
            unique=True,
        ),
        Index("ix_skill_versions_org_status", "organization_id", "status"),
    )


__all__ = ["ExperienceRecordModel", "SkillVersionModel"]
