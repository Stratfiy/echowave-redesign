"""After nobody answered: the agent comes back, says so, and offers a way on.

The rungs below ``ladder``, in order, each offered only when the one above it
was declined (or is not set up):

1. **Back to the agent with an honest explanation** -- "I couldn't reach
   anyone just now", never a silent return to the script.
2. **A callback**, with the number read back to the caller before it is
   written down, filed as a task on the team's board (so it is on Today).
3. **A message with a ticket number**, on SMS or WhatsApp, where the agent's
   owner switched that on.
4. **Voicemail for the team**: the caller's message, with a link to the
   transcript, on the agent's thread.

The model speaks to the caller; the order and the rules are kept here. The
``escalation_fallback`` tool only accepts the rung that is current (or
"declined", which moves to the next), and refuses a callback whose number was
not read back -- a callback to a mis-heard number is a promise broken twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

from loguru import logger

from api.services.compliance import dnd
from api.services.escalation.policy import EMERGENCY_CLASS, EscalationPolicy
from api.utils.phone_masking import last_four

CALLBACK = "callback"
TICKET = "ticket"
VOICEMAIL = "voicemail"
DECLINED = "declined"
ORDER = (CALLBACK, TICKET, VOICEMAIL)

#: How soon a callback task is due, when the caller names no time.
DEFAULT_CALLBACK_MINUTES = 60


def offers_for(policy: EscalationPolicy, *, can_message: bool) -> list[str]:
    out = []
    if policy.offer_callback:
        out.append(CALLBACK)
    if policy.ticket_channel != "off" and can_message:
        out.append(TICKET)
    if policy.team_voicemail:
        out.append(VOICEMAIL)
    return out


def explanation(
    *, reached_nobody: bool, outside_hours: bool, reason: str | None
) -> str:
    """What the agent says first when it is back on the line. Honest, short."""
    if outside_hours:
        line = "Nobody from the team is available right now"
    elif reached_nobody:
        line = "I tried, but I couldn't reach anyone from the team just now"
    else:
        line = "I couldn't connect you just now"
    tail = ""
    # A caller in distress is handled like an emergency: the same line.
    if reason in EMERGENCY_CLASS:
        tail = " If this is an emergency, please call 112 now."
    return f"{line}, I'm sorry.{tail}"


_OFFER_LINES = {
    CALLBACK: (
        "Offer a call back from the team. If they want one, confirm the number "
        "to ring by reading it back digit by digit and wait for them to say it "
        "is right, then call escalation_fallback with choice='callback', the "
        "number and number_read_back=true."
    ),
    TICKET: (
        "Offer to send them a message with a reference number so they can "
        "follow up. If they want it, call escalation_fallback with "
        "choice='ticket'."
    ),
    VOICEMAIL: (
        "Offer to take a message for the team. If they want to, let them say "
        "it, then call escalation_fallback with choice='voicemail' and their "
        "message in their own words."
    ),
}


def offer_instruction(offer: str | None) -> str:
    if offer is None:
        return (
            "There is nothing more to offer. Apologise once, ask if there is "
            "anything else you can help with yourself, and carry on."
        )
    return _OFFER_LINES[offer] + (
        " If they say no, call escalation_fallback with choice='declined'."
    )


@dataclass
class FallbackLadder:
    offers: list[str]
    index: int = 0
    chosen: str | None = None
    history: list[str] = field(default_factory=list)

    def current(self) -> str | None:
        if self.chosen is not None or self.index >= len(self.offers):
            return None
        return self.offers[self.index]

    def decline(self) -> str | None:
        current = self.current()
        if current is not None:
            self.history.append(f"{current}:declined")
            self.index += 1
        return self.current()

    def take(self, choice: str) -> None:
        self.chosen = choice
        self.history.append(choice)


class FallbackRefused(ValueError):
    """What the model is told when it asked for something out of order."""


# --- the rungs' effects -------------------------------------------------------


def due_at(minutes: int | None = None, *, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + timedelta(
        minutes=int(minutes or DEFAULT_CALLBACK_MINUTES)
    )


def normalise_callback_number(raw: str | None) -> str | None:
    normalised = dnd.normalise_number(raw)
    return dnd.to_dialable(normalised) if normalised else None


async def file_callback(
    *,
    organization_id: int,
    workflow_id: int | None,
    workflow_run_id: int | None,
    owner_user_id: int | None,
    number: str,
    card: dict[str, Any],
    window_minutes: int | None = None,
    create_task: Callable[..., Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """A task on the team's board: ring this number back, with the card.

    A task rather than a new kind of record: Today already lists the
    team's tasks, the board already has an owner and a Done, and a callback
    is exactly a thing somebody must do by a time.
    """
    if create_task is None:
        from api.db import db_client

        create_task = db_client.create_task
    caller = card.get("caller") or {}
    who = caller.get("name") or last_four(number)
    title = f"Call back {who}"[:200]
    lines = [
        f"Number (read back and confirmed by the caller): {number}",
        f"Why they wanted a person: {card.get('reason') or ''}",
        f"Summary: {card.get('summary') or ''}",
    ]
    if card.get("intent"):
        lines.append(f"They want: {card['intent']}")
    if card.get("transcript_url"):
        lines.append(f"Transcript: {card['transcript_url']}")
    task = await create_task(
        organization_id=organization_id,
        title=title,
        brief="\n".join(lines),
        from_workflow_id=workflow_id,
        created_by=owner_user_id,
        source_run_id=workflow_run_id,
        due_at=due_at(window_minutes),
        priority="high",
    )
    return {"task_id": getattr(task, "id", None), "title": title}


def ticket_reference(escalation_id: int | None, escalation_uuid: str | None) -> str:
    tail = (escalation_uuid or "").replace("-", "")[:6].upper()
    return f"ESC-{escalation_id or 0}-{tail}" if tail else f"ESC-{escalation_id or 0}"


def ticket_text(reference: str, business: str | None) -> str:
    who = business or "the team"
    return (
        f"Thanks for calling {who}. Your reference is {reference}. "
        "Someone from the team will follow up; quote this reference if you "
        "get in touch before then."
    )


async def leave_voicemail(
    *,
    organization_id: int,
    workflow_id: int | None,
    workflow_run_id: int | None,
    message: str,
    card: dict[str, Any],
    record: Callable[..., Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """The caller's message on the agent's thread, with the card and the
    transcript link, flagged for a person."""
    if record is None:
        from api.services.workflow import agent_timeline

        record = agent_timeline.record
    from api.enums import AgentEventKind

    caller = card.get("caller") or {}
    who = caller.get("name") or caller.get("number_masked") or "A caller"
    text = " ".join((message or "").split())[:1000]
    event_id = await record(
        organization_id=organization_id,
        kind=AgentEventKind.NEEDS_ATTENTION.value,
        summary=f"Message for the team from {who}: {text[:200]}",
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        payload={
            "voicemail": {"message": text, "at": datetime.now(UTC).isoformat()},
            "handoff": card,
            "transcript_url": card.get("transcript_url"),
        },
    )
    if event_id is None:
        logger.warning("A caller's message for the team could not be recorded")
    return {"event_id": event_id}


__all__ = [
    "CALLBACK",
    "DECLINED",
    "FallbackLadder",
    "FallbackRefused",
    "ORDER",
    "TICKET",
    "VOICEMAIL",
    "explanation",
    "file_callback",
    "leave_voicemail",
    "normalise_callback_number",
    "offer_instruction",
    "offers_for",
    "ticket_reference",
    "ticket_text",
]
