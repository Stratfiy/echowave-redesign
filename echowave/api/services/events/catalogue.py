"""The event catalogue: every analytics event Decibyl sends, in one place.

Handoff 36 and the design's "Metrics events and release proof". A small,
versioned list tied to outcomes, not one event per click. Each entry says:

* who owns it -- ``server`` events state business outcomes (a task finished,
  an approval was granted) and are only ever emitted by the backend from the
  authoritative change; ``client`` events describe intent (a person opened
  the approval sheet) and are the only ones the browser may send
  (``routes/controls.py``). A client can never claim an outcome.
* which properties it may carry -- the common typed set every event shares,
  plus its own. Anything else is refused (``envelope.build``), which is how
  a prompt, a transcript or an email address cannot ride along by accident.
* its schema version, bumped when its properties change meaning.

Names reconcile with the handoff's proposal and the existing ``PostHogEvent``
constants (``api/enums.py``): those older events keep flowing unchanged; the
catalogue's ``voice_session_*`` events sit beside ``call_*`` rather than
replacing them, so no dashboard loses its history the day this switches on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SERVER = "server"
CLIENT = "client"

#: Typed properties any event may carry (handoff 35, "Common event contract").
COMMON_PROPERTIES = frozenset(
    {"channel", "language", "agent_type", "status", "reason_code", "duration_ms"}
)

#: Property names no event may ever carry, whatever its entry says. A guard
#: against an entry added later that names one of them; the test suite also
#: checks every entry against this list.
FORBIDDEN_PROPERTIES = frozenset(
    {
        "prompt",
        "transcript",
        "audio",
        "body",
        "text",
        "message",
        "content",
        "email",
        "phone",
        "name",
        "key",
        "secret",
        "token",
        "password",
        "kyc",
        "card",
        "address",
        "error",
    }
)


@dataclass(frozen=True)
class EventSpec:
    name: str
    domain: str
    owner: str
    description: str
    properties: frozenset[str] = field(default_factory=frozenset)
    version: int = 1

    @property
    def allowed(self) -> frozenset[str]:
        return COMMON_PROPERTIES | self.properties


def _s(name, domain, description, *extra, version=1) -> EventSpec:
    return EventSpec(name, domain, SERVER, description, frozenset(extra), version)


def _c(name, domain, description, *extra, version=1) -> EventSpec:
    return EventSpec(name, domain, CLIENT, description, frozenset(extra), version)


_ENTRIES: tuple[EventSpec, ...] = (
    # Onboarding
    _s("invite_accepted", "onboarding", "A person redeemed an invitation."),
    _s("onboarding_completed", "onboarding", "A person finished first-run setup."),
    _s(
        "first_useful_task_completed",
        "onboarding",
        "A person's first task with persisted or reconciled evidence.",
    ),
    # Tasks -- from the task ledger's transitions.
    _s("task_started", "tasks", "A ledger task moved to running.", "task_kind"),
    _s(
        "task_completed",
        "tasks",
        "A ledger task completed, with evidence on the record.",
        "task_kind",
        "has_evidence",
    ),
    _s("task_failed", "tasks", "A ledger task failed.", "task_kind"),
    _s("task_cancelled", "tasks", "A ledger task was cancelled.", "task_kind"),
    # Approval -- from actions.py, the card a person settles.
    _s("approval_requested", "approval", "A card was proposed.", "action_kind"),
    _s("approval_granted", "approval", "A person confirmed a card.", "action_kind"),
    _s("approval_rejected", "approval", "A person declined a card.", "action_kind"),
    _s("approval_expired", "approval", "A card expired unanswered.", "action_kind"),
    # Voice
    _s("voice_session_started", "voice", "A live voice session began."),
    _s(
        "voice_session_ended",
        "voice",
        "A voice session ended; duration, disconnect reason, latency if known.",
        "first_response_ms",
    ),
    _s("voice_session_failed", "voice", "A voice session failed."),
    # Meetings
    _s("capture_started", "meetings", "Meeting capture began.", "audio_source"),
    _s("capture_failed", "meetings", "Meeting capture failed.", "audio_source"),
    _s("meeting_processed", "meetings", "A meeting record was produced."),
    _s("action_confirmed", "meetings", "One meeting action was confirmed."),
    # Reminders
    _s("reminder_scheduled", "reminders", "A reminder was scheduled."),
    _s(
        "reminder_delivered",
        "reminders",
        "Provider-confirmed delivery; accepted or unknown say so in status.",
    ),
    _s("reminder_failed", "reminders", "A reminder could not be delivered."),
    _s("reminder_cancelled", "reminders", "A reminder was cancelled."),
    # Connections
    _s("connection_started", "connections", "A person began connecting an app.", "app"),
    _s("connection_ready", "connections", "A connection is usable.", "app"),
    _s("connection_revoked", "connections", "A connection was revoked.", "app"),
    # Support
    _s("ticket_created", "support", "A support ticket was opened."),
    _s("support_action_requested", "support", "Staff asked to act for a customer."),
    _s("support_action_approved", "support", "A support action was approved."),
    _s("support_action_executed", "support", "A support action ran."),
    _s("support_action_failed", "support", "A support action failed."),
    _s("ticket_resolved", "support", "A ticket was resolved."),
    _s("ticket_reopened", "support", "A ticket was reopened."),
    # Quality
    _s(
        "feedback_submitted",
        "quality",
        "A person answered 'Was this useful?' (only when they answered).",
        "verdict",
        "reasons",
        "subject_kind",
    ),
    _s("evaluation_completed", "quality", "An evaluation run finished.", "score"),
    _s("regression_detected", "quality", "An evaluation found a regression."),
    # Learning (stream `learning`). Codes only: never the goal, the lesson
    # or an answer.
    _s(
        "learning_goal_started",
        "learning",
        "A person started a learning goal.",
        "has_material",
    ),
    _s(
        "learning_practice_evaluated",
        "learning",
        "One practice answer was marked against its rubric.",
        "outcome",
        "exercise_kind",
    ),
    _s("learning_goal_deleted", "learning", "A person deleted a learning goal."),
    # Finance
    _s("payment_succeeded", "finance", "A payment cleared.", "currency"),
    _s("payment_failed", "finance", "A payment failed.", "currency"),
    _s("refund_completed", "finance", "A refund completed.", "currency"),
    _s(
        "usage_cost_recorded",
        "finance",
        "A cost line: provider, unit, rate version, currency, estimated or not.",
        "provider",
        "usage_unit",
        "rate_version",
        "currency",
        "cost_status",
        # Stream ops: the amounts behind cost per success, in paise.
        "provider_cost_paise",
        "charged_paise",
        "uncosted_items",
    ),
    # Operations (stream ops): the operations themselves, so a funnel can
    # show when an incident, a rollback or a cost stop overlapped a drop.
    _s(
        "ops_command_executed",
        "ops",
        "A typed staff operation reached a final state.",
        "command",
    ),
    _s("cost_stop_engaged", "ops", "New billable work was stopped.", "scope"),
    _s("cost_stop_released", "ops", "A cost stop was released.", "scope"),
    # Client intent. The browser may send only these.
    _c("approval_viewed", "approval", "A person opened an approval preview."),
    _c("feedback_prompt_dismissed", "quality", "A person closed 'Was this useful?'."),
)

CATALOGUE: dict[str, EventSpec] = {entry.name: entry for entry in _ENTRIES}
if len(CATALOGUE) != len(_ENTRIES):  # pragma: no cover - caught at import
    raise RuntimeError("Two catalogue entries share a name")


def get(name: str) -> EventSpec:
    """The entry for ``name``; KeyError for a name the catalogue lacks."""
    return CATALOGUE[name]


def as_rows() -> list[dict]:
    """The catalogue as plain rows, for the staff endpoint and the docs."""
    return [
        {
            "name": e.name,
            "domain": e.domain,
            "owner": e.owner,
            "version": e.version,
            "description": e.description,
            "properties": sorted(e.allowed),
        }
        for e in _ENTRIES
    ]
