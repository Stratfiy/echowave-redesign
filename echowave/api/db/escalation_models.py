"""Tables for escalation v2 (services/escalation).

Two, each scoped by ``organization_id`` and read only through it:

* ``escalations`` -- one row per time a call was handed (or was about to be
  handed) to a person. The row *is* the state machine: ``requested ->
  dialling -> briefing -> bridged -> completed | failed``. ``idempotency_key``
  is unique, so two workers that see the same trigger write one row, and
  ``attempt_count`` is a compare-and-swap counter, so a retry that has
  already dialled attempt ``n`` cannot dial it again.
* ``call_escalation_outcomes`` -- one row per call that ran with the policy
  on: resolved by the agent, or escalated with a reason, whether the
  transfer reached a person and why not, and how long that took. For
  reporting later; nothing reads it on a live call.

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
    Text,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class EscalationModel(Base):
    """One handover of a caller to a person, and how it went."""

    __tablename__ = "escalations"

    id = Column(Integer, primary_key=True)
    #: The id the app and the carrier callbacks use. Never the integer id,
    #: which is guessable.
    escalation_uuid = Column(String(36), nullable=False, unique=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    workflow_run_id = Column(Integer, nullable=True)
    #: ``run:<id>:<n>`` -- the n-th escalation on that call. Unique, so a
    #: duplicate trigger finds the row the first one wrote.
    idempotency_key = Column(String(128), nullable=False, unique=True)
    #: requested | dialling | briefing | bridged | completed | failed
    state = Column(String(16), nullable=False, default="requested")
    failure_reason = Column(String(40), nullable=True)
    #: explicit_request | policy | repair_loop | low_confidence | frustration
    reason_code = Column(String(24), nullable=False)
    reason_detail = Column(String(200), nullable=True)
    #: ``auto`` (the policy decided) or ``tool`` (the agent's transfer tool).
    trigger = Column(String(8), nullable=False, default="auto")
    #: How many people have been dialled. Advanced by compare-and-swap only.
    attempt_count = Column(Integer, nullable=False, default=0)
    #: One entry per dial: masked target, transfer id, outcome, times.
    attempts = Column(JSON, nullable=False, default=list)
    #: The carrier's transfer id of the attempt in flight (or the one that
    #: reached a person), for callbacks that only know that.
    current_transfer_id = Column(String(64), nullable=True)
    #: The carrier's id for the human's leg once it answered.
    human_call_id = Column(String(128), nullable=True)
    #: callback | ticket | voicemail | declined, once the caller chose one.
    fallback = Column(String(16), nullable=True)
    handoff_card = Column(JSON, nullable=True)
    #: accepted | declined, pressed on the card by a person.
    human_response = Column(String(16), nullable=True)
    human_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: What the person said when they handed the caller back to the agent.
    outcome_note = Column(Text, nullable=True)
    #: The card on the agent's thread.
    timeline_event_id = Column(Integer, nullable=True)
    time_to_human_ms = Column(Integer, nullable=True)
    requested_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    bridged_at = Column(DateTime(timezone=True), nullable=True)
    handed_back_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_escalations_org_requested", "organization_id", "requested_at"),
        Index("ix_escalations_run", "workflow_run_id"),
        Index("ix_escalations_transfer", "current_transfer_id"),
    )


class CallEscalationOutcomeModel(Base):
    """How one call ended, as far as escalation is concerned."""

    __tablename__ = "call_escalation_outcomes"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    workflow_run_id = Column(Integer, nullable=False, unique=True)
    #: resolved_by_ai | escalated
    outcome = Column(String(24), nullable=False)
    reason_code = Column(String(24), nullable=True)
    #: bridged | failed | callback (no person was free, so none was dialled)
    transfer_result = Column(String(16), nullable=True)
    failure_reason = Column(String(40), nullable=True)
    fallback = Column(String(16), nullable=True)
    time_to_human_ms = Column(Integer, nullable=True)
    escalation_id = Column(
        Integer, ForeignKey("escalations.id", ondelete="SET NULL"), nullable=True
    )
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index(
            "ix_call_escalation_outcomes_org_recorded",
            "organization_id",
            "recorded_at",
        ),
    )


__all__ = ["CallEscalationOutcomeModel", "EscalationModel"]
