"""Tables for "call me when it's done" (services/call_when_done).

Three, each scoped by ``organization_id`` and read only through it:

* ``done_callbacks`` -- one per "call me when this is done": what the
  person asked to hear about (``subject``), and, once it finished, which
  completion it was (``finished_key``) and the call that says it.
* ``done_calls`` -- one per call. Several finished tasks share one: at most
  one call per person is ``queued`` at a time (a partial unique index), so
  two tasks that finish together are said together, however many workers
  record them.
* ``done_call_numbers`` -- the number a person confirmed, once, on a card,
  for these calls. Theirs, in that workspace.

Kept out of ``models.py`` (launch convention); ``models.py`` imports this
module at its end so the tables are on ``Base.metadata``.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
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


class DoneCallbackModel(Base):
    """A person asked to be rung when something finishes.

    ``state``: ``pending`` (waiting for it to finish) -> ``finished`` (it
    did; ``call_id`` names the call that says so) or ``cancelled``.
    ``subject`` is what to watch: ``task:<id>``, ``browser:<uuid>``,
    ``workflow:<id>`` or ``thread:<id|main>`` (the next thing to finish on
    that conversation). ``standing`` rows come from the person's standing
    preference rather than an ask, and are written already finished.
    """

    __tablename__ = "done_callbacks"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    thread_id = Column(String(64), nullable=True)
    subject = Column(String(80), nullable=False)
    state = Column(String(16), nullable=False, default="pending")
    standing = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: The completion that settled it (``task:12``), so the two timeline rows
    #: one finish writes settle it once.
    finished_key = Column(String(80), nullable=True)
    title = Column(String(200), nullable=True)
    #: One or two sentences, said on the call.
    summary = Column(Text, nullable=True)
    #: The task's output, for the call's follow-up questions. Bounded.
    output = Column(Text, nullable=True)
    needs_you = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    call_id = Column(
        Integer, ForeignKey("done_calls.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_done_callbacks_watch", "organization_id", "state", "subject"),
        Index(
            "uq_done_callbacks_finished",
            "organization_id",
            "user_id",
            "finished_key",
            unique=True,
            postgresql_where=text("finished_key IS NOT NULL"),
        ),
    )


class DoneCallModel(Base):
    """One call to one person, saying everything that finished for them.

    ``state``: ``queued`` -> ``calling`` -> ``answered`` | ``not_answered``
    | ``failed`` | ``unknown``; or ``notified`` when no call could be placed
    and the person was told in the app instead. ``calling`` means claimed
    and being dialled; ``unknown`` means a dial may have reached the carrier
    but nothing has proved what happened -- never re-dialled, reconciled
    against the run, and corrected by late evidence (``outcome_history``
    keeps every reading). ``not_answered`` only on evidence: a carrier
    no-answer, or a finished run nobody answered. ``due_at`` is when it may
    be placed: a short gather after the first finish, or 09:00 local when
    that falls outside calling hours. ``reason`` is a code for anything but
    answered.
    """

    __tablename__ = "done_calls"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    state = Column(String(16), nullable=False, default="queued")
    due_at = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String(32), nullable=True)
    workflow_run_id = Column(Integer, nullable=True)
    #: Where the thread notice went (the first callback's conversation).
    thread_id = Column(String(64), nullable=True)
    #: What was said, per channel, when it fell back to a notice.
    notified = Column(JSON, nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    placed_at = Column(DateTime(timezone=True), nullable=True)
    outcome_at = Column(DateTime(timezone=True), nullable=True)
    #: The person's local day whose daily-cap slot this call holds
    #: (``person_call_allowances``); NULL when it holds none.
    allowance_day = Column(Date, nullable=True)
    #: Every change of state after the dial, appended:
    #: ``[{"at", "from", "to", "reason", "source"}]``.
    outcome_history = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_done_calls_due", "state", "due_at"),
        Index(
            "uq_done_calls_one_queued",
            "organization_id",
            "user_id",
            unique=True,
            postgresql_where=text("state = 'queued'"),
        ),
    )


class DoneCallNumberModel(Base):
    """The number a person confirmed for these calls, on a card, once."""

    __tablename__ = "done_call_numbers"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: E.164, as ``dnd.to_dialable`` writes it.
    phone = Column(String(20), nullable=False)
    #: NULL while the card waits; set when the person confirms it.
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    card_event_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_done_call_number_person"
        ),
    )


class PersonCallAllowanceModel(Base):
    """How many "call me when it's done" calls one person has reserved on
    one of their local days, across every workspace they are rung from.

    Grown only by a conditional upsert that refuses past the cap, so the
    count is a reservation made before the dial, not a tally taken after
    it. Care's medicine reminder calls do not use it (see allowance.py).
    """

    __tablename__ = "person_call_allowances"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    local_day = Column(Date, primary_key=True)
    used = Column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
