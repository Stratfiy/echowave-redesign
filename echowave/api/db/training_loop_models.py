"""The table behind the training loop (services/training_loop).

``learning_events`` is one row per thing that happened to one suggestion or
action of one agent, in one workspace: shown, approved, rejected, edited then
approved, undone, a thumb, an owner's correction, a failed eval, an
escalation. It is written so a model can be fine-tuned on it later (SFT, and
preference pairs) and so a workspace can take it away (JSONL export), which
is why it holds the words and not only a verdict.

Rules the shape enforces:

* **Per workspace, always.** ``organization_id`` is not nullable, and every
  read names it. There is no query in the codebase that spans workspaces.
* **Idempotent.** ``(organization_id, event_type, subject_key)`` is unique, so
  a card settled twice, or a retry, is one row.
* **Consent is checked before a row is written.** While the workspace has
  "Use my feedback to improve my agents" off, nothing new is written. Turning
  it off with "Stop collecting" keeps the rows already there; "Stop and
  delete" archives them (``archived_at``): out of export, counts and
  training, kept only as the law requires, never hard-deleted
  (services/training_loop/consent.py).
* **Redacted before it lands.** The three text columns are written through
  ``services/training_loop/redact.py``; nothing reaches them raw.

Kept out of ``models.py`` (launch convention); ``models.py`` imports this
module at its end so the table is on ``Base.metadata``.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from api.db.models import Base


class LearningEventModel(Base):
    __tablename__ = "learning_events"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: The agent. Kept (as NULL) if the agent is deleted: the history is the
    #: workspace's, and its export should not shrink because an agent went.
    workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    #: The person who acted, when a person did.
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: suggestion_shown | approved | rejected | edited_then_approved | undone |
    #: thumbs_up | thumbs_down | owner_correction | eval_fail | escalation.
    #: A string, so an eleventh needs no migration.
    event_type = Column(String(24), nullable=False)
    #: Where it happened: edit_card | action_card | reply | eval | escalation.
    source = Column(String(24), nullable=False)
    #: ``agent``: about one agent. ``decibyl``: about Decibyl's own thread,
    #: which has no agent (``workflow_id`` is NULL).
    scope = Column(String(12), nullable=False, default="agent", server_default="agent")
    #: What makes this event the same event if it is written twice.
    subject_key = Column(String(128), nullable=False)
    #: A pointer to the thing judged (``agent_event:123``), never its words.
    input_ref = Column(String(160), nullable=True)
    #: Situations that are comparable: a rejected suggestion and a later
    #: approved one for the same step share a key, which is how a preference
    #: pair is found when the owner did not edit anything.
    group_key = Column(String(160), nullable=True)
    #: What the model was asked, as text. Redacted. NULL when declined.
    input_text = Column(Text, nullable=True)
    #: What the model produced. Redacted. NULL when declined.
    model_output = Column(Text, nullable=True)
    #: What the owner ended up with: the approved or edited version.
    owner_final = Column(Text, nullable=True)
    #: A short machine-readable note: a thumb's reason codes, an escalation's
    #: reason code, a failed eval's verdict. Redacted.
    detail = Column(String(200), nullable=True)
    model = Column(String(128), nullable=True)
    prompt_tokens = Column(BigInteger, nullable=True)
    completion_tokens = Column(BigInteger, nullable=True)
    #: granted | declined: the workspace's setting when this was written.
    consent_state = Column(String(12), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    #: Structured facts of the event as JSON, never free text (a routing
    #: decision's candidates, chosen model, latency, tokens, cost, outcomes).
    data = Column(JSONB, nullable=True)
    #: Set when the workspace chose "Stop and delete". An archived row is out
    #: of every export, count and training read, and is kept only as long as
    #: the law needs (``training_loop.ARCHIVE_RETENTION_DAYS``). It is never
    #: hard-deleted here.
    archived_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "event_type",
            "subject_key",
            name="uq_learning_events_once",
        ),
        Index(
            "ix_learning_events_org_agent",
            "organization_id",
            "workflow_id",
            "created_at",
        ),
        Index("ix_learning_events_org_type", "organization_id", "event_type"),
    )
