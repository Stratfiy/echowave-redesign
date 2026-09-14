"""Meter what a bot does when it is not on the phone.

KAN-56. A call is costed by the minute; everything else a bot does — answer
in a channel, run a routine, reach into outside software, help build itself
— reached no costing path at all. Each is one event with one price, and the
price is a credit figure decided on KAN-47 rather than a cost computed from
tokens: the customer can see an event and cannot see a token.

Every timeline kind either carries a price here or is marked *included*, and
a test holds that the table covers the enum: an event that is neither is an
event nobody decided about, which is how "free" happens by accident.

One ledger row per event, keyed on the event's own id so a retried task
debits nothing twice — the same rule ``messaging_charges`` follows. Internal
accounts are never charged, the same rule a call follows.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CreditLedgerModel
from api.enums import AgentEventKind, CreditLedgerKind
from api.services.billing.credits import PAISE_PER_CREDIT

TEXT_REPLY = "text_reply"
KNOWLEDGE_ANSWER = "knowledge_answer"
ROUTINE_RUN = "routine_run"
TOOL_CALL = "tool_call"
TOOL_CALL_PREMIUM = "tool_call_premium"
BUILDER_MESSAGE = "builder_message"
#: Verifying a phone number by a voice call that reads the code out. The
#: first ``FREE_NUMBER_VERIFICATIONS`` numbers an account verifies are free.
NUMBER_VERIFICATION = "number_verification"
FREE_NUMBER_VERIFICATIONS = 2

#: Credits per event. Decided 14 Sept 2026 (KAN-47, study §11); a change here
#: is a published-price change and needs a note there.
EVENT_CREDITS: dict[str, int] = {
    TEXT_REPLY: 1,
    KNOWLEDGE_ANSWER: 2,
    ROUTINE_RUN: 2,
    TOOL_CALL: 1,
    TOOL_CALL_PREMIUM: 3,
    BUILDER_MESSAGE: 5,
    NUMBER_VERIFICATION: 2,
}

#: What each event is called on a statement.
EVENT_LABELS: dict[str, str] = {
    TEXT_REPLY: "Text reply",
    KNOWLEDGE_ANSWER: "Knowledge answer",
    ROUTINE_RUN: "Routine run",
    TOOL_CALL: "Tool call",
    TOOL_CALL_PREMIUM: "Tool call (premium connector)",
    BUILDER_MESSAGE: "Builder message past the allowance",
    NUMBER_VERIFICATION: "Number verification past the first two",
}

#: Connector toolkit slugs billed at the premium rate. Empty until the list
#: is decided (open on KAN-47); ``PREMIUM_CONNECTORS=salesforce,sap`` names
#: them without a release. Lower-case slugs, as Composio names them.
PREMIUM_CONNECTORS: frozenset[str] = frozenset(
    s.strip().lower()
    for s in os.getenv("PREMIUM_CONNECTORS", "").split(",")
    if s.strip()
)

#: The function name of knowledge-base retrieval, as the runner records it
#: on a turn's events. A turn that called it is a knowledge answer.
KNOWLEDGE_TOOL_NAME = "retrieve_from_knowledge_base"

#: Marker for a timeline kind that costs nothing on its own — because the
#: thing it reports is priced elsewhere (a call, by the minute) or is the
#: system talking to the operator.
INCLUDED = "included"

#: Every timeline kind, priced or marked included. Kinds that report a
#: charged event map to that event; the rest are included. ``MESSAGE`` is a
#: text reply unless the row's payload says it was a knowledge answer.
TIMELINE_PRICES: dict[str, str] = {
    AgentEventKind.CALL_STARTED.value: INCLUDED,
    AgentEventKind.CALL_ANSWERED.value: INCLUDED,
    AgentEventKind.CALLER_WANTED.value: INCLUDED,
    AgentEventKind.AGENT_ACTED.value: TOOL_CALL,
    AgentEventKind.OUTCOME_FILED.value: INCLUDED,
    AgentEventKind.ESCALATED.value: INCLUDED,
    AgentEventKind.CALL_ENDED.value: INCLUDED,
    AgentEventKind.CREDITS_HELD.value: INCLUDED,
    AgentEventKind.CREDITS_SETTLED.value: INCLUDED,
    AgentEventKind.NEEDS_ATTENTION.value: INCLUDED,
    AgentEventKind.NEEDS_DECISION.value: INCLUDED,
    AgentEventKind.NEEDS_SECRET.value: INCLUDED,
    AgentEventKind.ACTION_PROPOSED.value: INCLUDED,
    AgentEventKind.EDIT_PROPOSED.value: INCLUDED,
    AgentEventKind.ACTIVITY.value: INCLUDED,
    AgentEventKind.MEMORY_LEARNED.value: INCLUDED,
    AgentEventKind.ROUTINE_FIRED.value: INCLUDED,
    AgentEventKind.ROUTINE_SKIPPED.value: INCLUDED,
    AgentEventKind.DELIVERABLE.value: ROUTINE_RUN,
    AgentEventKind.COULD_NOT.value: INCLUDED,
    AgentEventKind.MESSAGE.value: TEXT_REPLY,
}


def credits_for(event: str) -> int:
    return EVENT_CREDITS[event]


def paise_for(event: str, quantity: int = 1) -> int:
    return credits_for(event) * PAISE_PER_CREDIT * max(1, int(quantity))


def tool_call_event(toolkit: str | None) -> str:
    """Which tool-call rate a connector is billed at."""
    return (
        TOOL_CALL_PREMIUM
        if (toolkit or "").strip().lower() in PREMIUM_CONNECTORS
        else TOOL_CALL
    )


def turn_used_knowledge(turn: dict | None) -> bool:
    """Whether a completed text-chat turn called knowledge retrieval."""
    for event in (turn or {}).get("events") or []:
        if not isinstance(event, dict) or event.get("type") != "tool_call_started":
            continue
        payload = event.get("payload") or {}
        if payload.get("function_name") == KNOWLEDGE_TOOL_NAME:
            return True
    return False


def event_for_turn(turn: dict | None) -> str:
    return KNOWLEDGE_ANSWER if turn_used_knowledge(turn) else TEXT_REPLY


def last_turn_of(text_session) -> dict | None:
    """The most recent turn on a text-chat session, or None.

    Defensive on purpose: the hook that reads this runs inside the reply path,
    and a session shaped differently (a stub, an older row with no turns)
    must cost a text reply rather than end the answer.
    """
    data = getattr(text_session, "session_data", None)
    if not isinstance(data, dict):
        return None
    turns = data.get("turns")
    if not isinstance(turns, list) or not turns:
        return None
    last = turns[-1]
    return last if isinstance(last, dict) else None


def timeline_price(kind: str, payload: dict | None = None) -> dict:
    """What a timeline row cost, for the screen: ``{"credits": n}`` or
    ``{"included": True}``.

    A row whose payload carries ``credits`` (a message that was a knowledge
    answer, a premium tool call) reports that; otherwise the kind's price. An
    unknown kind — written by a newer deploy — is reported included rather
    than refused, so a screen never blanks over a price it cannot name.
    """
    stamped = (payload or {}).get("credits")
    if isinstance(stamped, int) and not isinstance(stamped, bool) and stamped >= 0:
        return {"credits": stamped, "included": stamped == 0}
    price = TIMELINE_PRICES.get(kind, INCLUDED)
    if price == INCLUDED:
        return {"credits": 0, "included": True}
    return {"credits": EVENT_CREDITS[price], "included": False}


async def _balance_paise(session: AsyncSession, *, organization_id: int) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
                CreditLedgerModel.organization_id == organization_id
            )
        )
        or 0
    )


async def charge(
    session: AsyncSession,
    *,
    organization_id: int,
    event: str,
    ref_id: str,
    quantity: int = 1,
    note: str | None = None,
) -> int:
    """Debit one event. Returns the paise debited (0 if already done, or the
    account is internal).

    Keyed on ``(event, ref_id)``: a retried task, a redelivered job, a
    handler that ran twice — all find the row and write nothing. The ref is
    the event's own id (a run, a turn, a tool call), never a timestamp.
    """
    from api.services.billing.internal_accounts import is_internal

    if event not in EVENT_CREDITS:
        raise KeyError(f"{event!r} is not a metered event; see events.EVENT_CREDITS")
    if not ref_id:
        return 0
    if await is_internal(session, organization_id):
        return 0
    existing = await session.scalar(
        select(CreditLedgerModel.id).where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.USAGE.value,
            CreditLedgerModel.ref_type == event,
            CreditLedgerModel.ref_id == ref_id[:64],
        )
    )
    if existing is not None:
        logger.debug("{} {} already debited", event, ref_id)
        return 0
    amount = paise_for(event, quantity)
    balance = await _balance_paise(session, organization_id=organization_id)
    credits = amount // PAISE_PER_CREDIT
    label = EVENT_LABELS[event]
    if quantity > 1:
        label += f" ×{quantity}"
    session.add(
        CreditLedgerModel(
            organization_id=organization_id,
            delta_paise=-amount,
            kind=CreditLedgerKind.USAGE.value,
            ref_type=event,
            ref_id=ref_id[:64],
            balance_after_paise=balance - amount,
            note=f"{label} · {credits} credit{'s' if credits != 1 else ''}"
            + (f" · {note}" if note else ""),
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return amount


async def charge_in_own_session(
    *,
    organization_id: int | None,
    event: str,
    ref_id: str,
    quantity: int = 1,
    note: str | None = None,
) -> int:
    """``charge`` from a runtime path that holds no session. Never raises: a
    charge that fails is logged loudly and the bot's work stands — the
    ledger is reconciled, a customer's answer is not withdrawn."""
    if not organization_id:
        return 0
    try:
        from api.db import db_client

        async with db_client.async_session() as session:
            amount = await charge(
                session,
                organization_id=organization_id,
                event=event,
                ref_id=ref_id,
                quantity=quantity,
                note=note,
            )
            await session.commit()
            return amount
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.error(
            "Could not charge {} {} for org {}: {}", event, ref_id, organization_id, exc
        )
        return 0
