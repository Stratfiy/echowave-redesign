"""The reminder card: exactly what will ring, shown before anything is saved.

    Thu 10 Oct 2026, 08:30 IST (Asia/Kolkata) · +91 98••••3210 · Tamil
    "Send the proposal" · once · if no answer: one retry at 08:45, then a
    notification

Confirm saves a schedule whose version is the card's version
(``schedule.save``); Not now saves nothing; Put it back (after it is saved)
cancels the schedule, so nothing more rings. A time asked for outside
calling hours is shown as such, with 09:00 offered in its place
(``draft``): the person confirms the moved time or asks for another.

The card is the person's alone (``only_user_id``, ``private_to``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from api.services.call_when_done import agent as done_agent
from api.services.reminder_calls import ReminderCallError, draft, number, policy
from api.services.today.scope import full_local


def _now() -> datetime:
    return datetime.now(UTC)


#: The draft fields a card carries in its arguments (and so in its version).
FIELDS = (
    "title",
    "language",
    "timezone",
    "local_time",
    "recurrence",
    "weekday",
    "date",
    "asked_time",
    "first_due_at",
    "phone",
    "replaces",
)

RECURRENCE_WORDS = {
    "once": "once",
    "daily": "every day",
    "weekdays": "every weekday",
}


def _recurrence_words(args: dict[str, Any]) -> str:
    recurrence = args.get("recurrence") or "once"
    if recurrence == "weekly":
        weekday = args.get("weekday")
        name = draft.WEEKDAYS[int(weekday)].capitalize() if weekday is not None else ""
        return f"every {name}" if name else "every week"
    return RECURRENCE_WORDS.get(recurrence, recurrence)


def retry_words(due: datetime, zone_name: str) -> str:
    if policy.MAX_RETRIES <= 0:
        return "if no answer: a notification"
    later = (due + timedelta(minutes=policy.RETRY_GAP_MINUTES)).astimezone(
        ZoneInfo(zone_name)
    )
    tries = "one retry" if policy.MAX_RETRIES == 1 else f"{policy.MAX_RETRIES} retries"
    return f"if no answer: {tries} at {later:%H:%M}, then a notification"


def summary(args: dict[str, Any]) -> tuple[str, str]:
    """``(label, effect)``: the card's two lines, from its arguments only."""
    due = datetime.fromisoformat(str(args["first_due_at"]))
    zone_name = str(args["timezone"])
    when = full_local(due, zone_name)
    language = done_agent.LANGUAGE_NAMES[done_agent.language_tag(args.get("language"))]
    label = f"{when} · {number.masked(str(args['phone']))} · {language}"
    effect = (
        f"“{args['title']}” · {_recurrence_words(args)} · {retry_words(due, zone_name)}. "
        f"Calls ring only between {policy.window_words()} and never on a number on "
        "this workspace's do-not-call list. The call says it is Decibyl and checks "
        "it is you before it reads your reminder."
    )
    return label, effect


def outside_window_words(asked: str, offered: str) -> str:
    """Why the time asked for cannot ring, and what the card offers instead
    (decision D2: the window is ``policy.CALLING_WINDOW_START``-``_END``)."""
    return (
        f"You asked for {asked}, which is outside calling hours: reminder "
        f"calls ring only between {policy.window_words()} your time, so "
        f"{asked} can't ring. This card is for {offered}, the nearest time "
        "that can. Confirm it, or say another time."
    )


def resolve(arguments: dict[str, Any], why: str) -> dict[str, Any]:
    """What the card shows. Raises ReminderCallError."""
    from api.services.workflow import actions

    try:
        person = int(arguments.get("person_user_id") or 0)
    except (TypeError, ValueError):
        person = 0
    if not person:
        raise ReminderCallError("Only a signed-in person can be called.")
    args: dict[str, Any] = {k: arguments.get(k) for k in FIELDS}
    args["person_user_id"] = person
    args["phone"] = number.clean(str(args.get("phone") or ""))
    if (
        not args.get("title")
        or not args.get("first_due_at")
        or not args.get("timezone")
    ):
        raise ReminderCallError("That reminder is missing its words or its time.")
    label, effect = summary(args)
    asked = args.get("asked_time")
    because = why or "You asked Decibyl to call you with a reminder."
    if asked:
        because = outside_window_words(str(asked), str(args["local_time"]))
    return {
        "action": actions.REMINDER_CALL,
        "args": args,
        "label": label,
        "why": because,
        "effect": effect,
        "reversible": True,
        "state": actions.PROPOSED,
        "only_user_id": person,
        "private_to": person,
        "audit_subject": "Reminder call",
    }


async def propose(
    organization_id: int,
    user_id: int,
    cleaned: dict[str, Any],
    *,
    thread_id: str | None,
) -> dict[str, Any]:
    """Put the reminder card on the thread. Returns what ``actions.propose``
    says (status, event id)."""
    from api.services.workflow import actions, agent_timeline

    with agent_timeline.in_thread(thread_id):
        told = await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments={
                **{k: cleaned.get(k) for k in FIELDS},
                "action": actions.REMINDER_CALL,
                "person_user_id": user_id,
            },
            in_channel=False,
        )
    if told.get("status") not in ("proposed", "already_proposed"):
        raise ReminderCallError(
            told.get("reason") or "The reminder could not be shown."
        )
    return told


async def execute(
    organization_id: int, payload: dict[str, Any], event_id: int | None
) -> str:
    """Confirmed: save the schedule, at the card's version."""
    from api.services.reminder_calls import schedule
    from api.services.workflow import actions

    args = dict(payload.get("args") or {})
    version = (
        (payload.get("confirmed") or {}).get("version")
        or payload.get("version")
        or actions.payload_version(payload)
    )
    thread_id = await number.thread_of(organization_id, event_id)
    saved = await schedule.save(
        organization_id,
        int(args["person_user_id"]),
        args,
        version=str(version),
        card_event_id=event_id,
        thread_id=thread_id,
        now=_now(),
    )
    due = saved.next_due_at
    when = full_local(due, saved.timezone) if due else "its time"
    return f"Set. Decibyl will call {number.masked(saved.phone)} at {when}."


async def reverse(organization_id: int, payload: dict[str, Any]) -> None:
    """Put back: the schedule this card saved is cancelled. Nothing more
    rings for it; an attempt already queued is skipped at the gate."""
    from api.services.reminder_calls import schedule

    args = dict(payload.get("args") or {})
    await schedule.cancel_for_card(
        organization_id, int(args.get("person_user_id") or 0), payload
    )
