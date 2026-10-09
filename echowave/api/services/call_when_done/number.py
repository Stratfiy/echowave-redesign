"""The number Decibyl rings when a task finishes: confirmed once, on a card.

A person's account has no verified phone of its own (``verified_numbers``
is the workspace's, for test calls), so the number is asked for in the
thread and confirmed on a card that shows it -- the consent pattern care's
reminder calls use (services/care/cards.py). The card is the person's alone
(``only_user_id`` and ``private_to``): a colleague who can read the thread
can neither see the number nor confirm it.

``services/workflow/actions.py`` owns the card's life (proposed -> armed ->
done, undo, run once); this module says what it shows and does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from api import constants
from api.db import db_client
from api.db.call_when_done_models import DoneCallNumberModel
from api.services.call_when_done import CallWhenDoneError
from api.services.compliance import dnd


def clean(raw: str | None) -> str:
    """E.164, or CallWhenDoneError. The same normalisation the do-not-call
    list matches on, so the number checked is the number dialled."""
    dialable = dnd.to_dialable(dnd.normalise_number(raw))
    if not dialable:
        raise CallWhenDoneError("That does not look like a phone number.")
    return dialable


def masked(phone: str) -> str:
    """+91 98••••3210: enough to recognise, not enough to copy."""
    digits = "".join(c for c in phone or "" if c.isdigit())
    if len(digits) < 8:
        return "your number"
    country = digits[:-10]
    national = digits[-10:]
    lead = f"+{country} " if country else ""
    return f"{lead}{national[:2]}••••{national[-4:]}"


def window() -> str:
    """ "9:00 and 21:00" -- the calling window, as configured."""
    return (
        f"{hhmm(constants.CALLING_HOURS_START)} and {hhmm(constants.CALLING_HOURS_END)}"
    )


def hhmm(value: str) -> str:
    hour, _, minute = (value or "").partition(":")
    try:
        return f"{int(hour)}:{int(minute or 0):02d}"
    except ValueError:
        return value


async def confirmed(organization_id: int, user_id: int) -> str | None:
    """The person's confirmed number in this workspace, or None."""
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(DoneCallNumberModel).where(
                DoneCallNumberModel.organization_id == organization_id,
                DoneCallNumberModel.user_id == user_id,
                DoneCallNumberModel.confirmed_at.is_not(None),
            )
        )
    return row.phone if row else None


async def propose(
    organization_id: int, user_id: int, phone: str, *, thread_id: str | None
) -> int | None:
    """Put the number card on the thread. Returns the card's id."""
    from api.services.workflow import actions, agent_timeline

    with agent_timeline.in_thread(thread_id):
        told = await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments={
                "action": actions.CALL_WHEN_DONE_NUMBER,
                "phone": phone,
                "person_user_id": user_id,
            },
            in_channel=False,
        )
    if told.get("status") not in ("proposed", "already_proposed"):
        raise CallWhenDoneError(
            told.get("reason") or "The number could not be asked for."
        )
    return told.get("event_id")


def resolve(arguments: dict[str, Any], why: str) -> dict[str, Any]:
    """What the card shows. Raises CallWhenDoneError."""
    from api.services.workflow import actions

    phone = clean(str(arguments.get("phone") or ""))
    try:
        person = int(arguments.get("person_user_id") or 0)
    except (TypeError, ValueError):
        person = 0
    if not person:
        raise CallWhenDoneError("Only a signed-in person can be called.")
    shown = masked(phone)
    return {
        "action": actions.CALL_WHEN_DONE_NUMBER,
        "args": {"phone": phone, "person_user_id": person},
        "label": f"Ring me on {shown} when my tasks finish",
        "why": why or "You asked Decibyl to call you when a task is done.",
        "effect": (
            f"Decibyl will ring {shown} when a task you asked about finishes, "
            f"only between {window()} and never if the number is on this "
            "workspace's do-not-call list. The call says it is Decibyl, gives "
            "the result, and you can say 'tell me more'."
        ),
        "reversible": True,
        "state": actions.PROPOSED,
        "only_user_id": person,
        "private_to": person,
        "audit_subject": "Calls when tasks finish: a number",
    }


async def execute(
    organization_id: int, payload: dict[str, Any], event_id: int | None
) -> str:
    args = dict(payload.get("args") or {})
    phone = clean(args.get("phone"))
    person = int(args["person_user_id"])
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        await session.execute(
            insert(DoneCallNumberModel)
            .values(
                organization_id=organization_id,
                user_id=person,
                phone=phone,
                confirmed_at=now,
                card_event_id=event_id,
                created_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_done_call_number_person",
                set_={"phone": phone, "confirmed_at": now, "card_event_id": event_id},
            )
        )
        await session.commit()
    return f"Saved. Decibyl will ring {masked(phone)} when your tasks finish."


async def reverse(organization_id: int, payload: dict[str, Any]) -> None:
    """Undo: the number is no longer rung."""
    args = dict(payload.get("args") or {})
    async with db_client.async_session() as session:
        await session.execute(
            delete(DoneCallNumberModel).where(
                DoneCallNumberModel.organization_id == organization_id,
                DoneCallNumberModel.user_id == int(args.get("person_user_id") or 0),
                DoneCallNumberModel.phone == str(args.get("phone") or ""),
            )
        )
        await session.commit()
