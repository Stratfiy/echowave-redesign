"""The number a reminder call rings: confirmed once, on a card, by an adult.

The card shows the number masked and carries the confirmation "I am 18 or
over" (``policy.ADULT_ATTESTATION``, decision D4): the card's Confirm is
refused unless that box is ticked (``actions.settle(attested=True)``), and
the confirmation is stored with the number (``adult_confirmed_at``). The
gate refuses ``not_adult`` for a number without it, so no path rings one.

The card is the person's alone (``only_user_id`` and ``private_to``). It may
carry the reminder the person asked for (``then``): once the number is
confirmed, that reminder's own card goes on the same thread, so asking for a
first reminder call is two confirmations in one place, never a dead end.

Its own table (``reminder_call_numbers``), not call-when-done's: confirming
a number for reminders does not make it the number rung when tasks finish.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.reminder_call_models import ReminderCallNumberModel
from api.services.call_when_done import number as done_number
from api.services.reminder_calls import ReminderCallError, policy


def clean(raw: str | None) -> str:
    try:
        return done_number.clean(raw)
    except ValueError as exc:
        raise ReminderCallError(str(exc)) from exc


def masked(phone: str) -> str:
    return done_number.masked(phone)


async def on_file(organization_id: int, user_id: int) -> dict[str, Any] | None:
    """The person's confirmed number here: ``{"phone", "adult"}``, or None."""
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(ReminderCallNumberModel).where(
                ReminderCallNumberModel.organization_id == organization_id,
                ReminderCallNumberModel.user_id == user_id,
                ReminderCallNumberModel.confirmed_at.is_not(None),
            )
        )
    if row is None:
        return None
    return {"phone": row.phone, "adult": row.adult_confirmed_at is not None}


async def ready(organization_id: int, user_id: int) -> str | None:
    """The number a reminder call may ring, or None: confirmed, and (with
    D4) confirmed by an adult."""
    found = await on_file(organization_id, user_id)
    if found is None:
        return None
    if policy.REQUIRE_ADULT_CONFIRMATION and not found["adult"]:
        return None
    return found["phone"]


async def propose(
    organization_id: int,
    user_id: int,
    phone: str,
    *,
    thread_id: str | None,
    then: dict[str, Any] | None = None,
) -> int | None:
    """Put the number card on the thread. Returns the card's id."""
    from api.services.workflow import actions, agent_timeline

    with agent_timeline.in_thread(thread_id):
        told = await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments={
                "action": actions.REMINDER_CALL_NUMBER,
                "phone": phone,
                "person_user_id": user_id,
                **({"then": then} if then else {}),
            },
            in_channel=False,
        )
    if told.get("status") not in ("proposed", "already_proposed"):
        raise ReminderCallError(
            told.get("reason") or "The number could not be asked for."
        )
    return told.get("event_id")


def resolve(arguments: dict[str, Any], why: str) -> dict[str, Any]:
    """What the card shows. Raises ReminderCallError."""
    from api.services.workflow import actions

    phone = clean(str(arguments.get("phone") or ""))
    try:
        person = int(arguments.get("person_user_id") or 0)
    except (TypeError, ValueError):
        person = 0
    if not person:
        raise ReminderCallError("Only a signed-in person can be called.")
    then = arguments.get("then")
    shown = masked(phone)
    payload: dict[str, Any] = {
        "action": actions.REMINDER_CALL_NUMBER,
        "args": {
            "phone": phone,
            "person_user_id": person,
            **({"then": dict(then)} if isinstance(then, dict) else {}),
        },
        "label": f"Ring me on {shown} for reminder calls I ask for",
        "why": why or "You asked Decibyl to call you with a reminder.",
        "effect": (
            f"Decibyl will ring {shown} only for reminders you confirm on a "
            f"card, only between {policy.window_words()}, and never if the "
            "number is on this workspace's do-not-call list."
        ),
        "reversible": True,
        "state": actions.PROPOSED,
        "only_user_id": person,
        "private_to": person,
        "audit_subject": "Reminder calls: a number",
    }
    if policy.REQUIRE_ADULT_CONFIRMATION:
        payload["attestation"] = policy.ADULT_ATTESTATION
    return payload


async def execute(
    organization_id: int, payload: dict[str, Any], event_id: int | None
) -> str:
    args = dict(payload.get("args") or {})
    phone = clean(args.get("phone"))
    person = int(args["person_user_id"])
    attested = bool((payload.get("confirmed") or {}).get("attested"))
    if policy.REQUIRE_ADULT_CONFIRMATION and not attested:
        # ``settle`` refuses a Confirm without the box; this is the second
        # lock, for any path that reaches here another way.
        raise ReminderCallError(f"Confirm “{policy.ADULT_ATTESTATION}” first.")
    now = datetime.now(UTC)
    adult_at = now if attested else None
    async with db_client.async_session() as session:
        await session.execute(
            insert(ReminderCallNumberModel)
            .values(
                organization_id=organization_id,
                user_id=person,
                phone=phone,
                confirmed_at=now,
                adult_confirmed_at=adult_at,
                card_event_id=event_id,
                created_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_reminder_call_number_person",
                set_={
                    "phone": phone,
                    "confirmed_at": now,
                    "adult_confirmed_at": adult_at,
                    "card_event_id": event_id,
                },
            )
        )
        await session.commit()
    then = args.get("then")
    if isinstance(then, dict) and then:
        await _propose_the_reminder(organization_id, person, phone, then, event_id)
        return f"Saved. Decibyl will ring {masked(phone)} for your reminder calls. Your reminder is on the next card."
    return f"Saved. Decibyl will ring {masked(phone)} for your reminder calls."


async def _propose_the_reminder(
    organization_id: int,
    user_id: int,
    phone: str,
    draft: dict[str, Any],
    event_id: int | None,
) -> None:
    """The reminder the person asked for, on its own card, on the number
    card's thread. Never raises: the number is saved either way."""
    from loguru import logger

    from api.services.reminder_calls import cards

    try:
        thread_id = await thread_of(organization_id, event_id)
        await cards.propose(
            organization_id, user_id, {**draft, "phone": phone}, thread_id=thread_id
        )
    except Exception as exc:  # noqa: BLE001 - the number card already did its job
        logger.warning("reminder_calls: could not propose the reminder card: {}", exc)


async def thread_of(organization_id: int, event_id: int | None) -> str | None:
    if not event_id:
        return None
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    return getattr(event, "thread_id", None) if event is not None else None


async def reverse(organization_id: int, payload: dict[str, Any]) -> None:
    """Undo: the number is no longer rung for reminders (the gate refuses
    ``no_number`` for every reminder that named it)."""
    args = dict(payload.get("args") or {})
    async with db_client.async_session() as session:
        await session.execute(
            update(ReminderCallNumberModel)
            .where(
                ReminderCallNumberModel.organization_id == organization_id,
                ReminderCallNumberModel.user_id == int(args.get("person_user_id") or 0),
                ReminderCallNumberModel.phone == str(args.get("phone") or ""),
            )
            .values(confirmed_at=None, adult_confirmed_at=None)
        )
        await session.commit()
