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

from api import constants
from api.db.models import CreditLedgerModel
from api.enums import AgentEventKind, CreditLedgerKind
from api.services.billing.credits import PAISE_PER_CREDIT

TEXT_REPLY = "text_reply"
KNOWLEDGE_ANSWER = "knowledge_answer"
ROUTINE_RUN = "routine_run"
#: A trigger fired and the bot did one turn on the event (KAN-137, study §25).
TRIGGER_RUN = "trigger_run"
#: A bot did a task from the board: one hand-off, one turn (KAN-140, study §26).
TASK_RUN = "task_run"
#: A bot ran one script in the sandbox (Step 20, Code Mode). Decided 15 Sept
#: 2026: 4 credits, on Everyday and above, and the app calls the script
#: makes from inside are not counted -- that is the whole saving for a
#: customer whose routine used to be two hundred tool calls.
SCRIPT_RUN = "script_run"
TOOL_CALL = "tool_call"
TOOL_CALL_PREMIUM = "tool_call_premium"
BUILDER_MESSAGE = "builder_message"
#: Verifying a phone number by a voice call that reads the code out. The
#: first ``FREE_NUMBER_VERIFICATIONS`` numbers an account verifies are free.
NUMBER_VERIFICATION = "number_verification"
#: Sarvam translate or transliterate, per 100 characters rounded up (KAN-104).
TRANSLATION = "translation"
#: Transcribing an uploaded recording or an imported dialer call (D-1). Only
#: charged while the charge rule is on; its figure lives in ``exchange``.
TRANSCRIPTION = "transcription_minute"
#: The unit a translation is billed in.
TRANSLATION_CHARS_PER_CREDIT = 100
FREE_NUMBER_VERIFICATIONS = 2

#: Credits per event. Decided 14 Sept 2026 (KAN-47, study §11); a change here
#: is a published-price change and needs a note there.
EVENT_CREDITS: dict[str, int] = {
    TEXT_REPLY: 1,
    KNOWLEDGE_ANSWER: 2,
    ROUTINE_RUN: 2,
    TRIGGER_RUN: 1,
    TASK_RUN: 1,
    SCRIPT_RUN: 4,
    TOOL_CALL: 1,
    TOOL_CALL_PREMIUM: 3,
    BUILDER_MESSAGE: 5,
    NUMBER_VERIFICATION: 2,
    TRANSLATION: 1,
}

#: What each event is called on a statement.
EVENT_LABELS: dict[str, str] = {
    TEXT_REPLY: "Text reply",
    KNOWLEDGE_ANSWER: "Knowledge answer",
    ROUTINE_RUN: "Routine run",
    TRIGGER_RUN: "Trigger run",
    TASK_RUN: "Task run",
    SCRIPT_RUN: "Script run",
    TOOL_CALL: "Tool call",
    TOOL_CALL_PREMIUM: "Tool call (premium connector)",
    BUILDER_MESSAGE: "Builder message past the allowance",
    NUMBER_VERIFICATION: "Number verification past the first two",
    TRANSLATION: "Translation",
    TRANSCRIPTION: "Transcription",
}

#: Connector toolkit slugs billed at the premium rate. ``PREMIUM_CONNECTORS=
#: salesforce,sap`` replaces the default list without a release. Lower-case
#: slugs, as Composio names them.
#: Decided 14 Sept (KAN-47): "reading or writing a system your business runs
#: on is 3 credits; everything else is 1." CRMs, ERP and accounting, commerce
#: and logistics, payments, helpdesk. Composio toolkit slugs; a slug not in
#: Composio's catalogue simply never matches, and the docs list is the one a
#: customer reads.
DEFAULT_PREMIUM_CONNECTORS: frozenset[str] = frozenset(
    {
        # CRM
        "salesforce",
        "hubspot",
        "zoho_crm",
        "zoho_bigin",
        "freshsales",
        "leadsquared",
        # ERP and accounting
        "sap",
        "tally",
        "zoho_books",
        "zoho_invoice",
        "quickbooks",
        # Commerce and logistics
        "shopify",
        "woocommerce",
        "shiprocket",
        "delhivery",
        # Payments
        "razorpay",
        "stripe",
        "cashfree",
        # Helpdesk
        "zendesk",
        "freshdesk",
        "zoho_desk",
    }
)

PREMIUM_CONNECTORS: frozenset[str] = (
    frozenset(
        s.strip().lower()
        for s in os.getenv("PREMIUM_CONNECTORS", "").split(",")
        if s.strip()
    )
    or DEFAULT_PREMIUM_CONNECTORS
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
    # A card offering to connect an app. Included: putting the offer on the
    # thread costs nothing, and the sign-in it leads to is the vendor's.
    AgentEventKind.CONNECTOR_OFFERED.value: INCLUDED,
    AgentEventKind.ACTIVITY.value: INCLUDED,
    # The private browser's panel. Whether browsing is charged is the
    # founder's decision (LAUNCH-PLAN, decisions open); until then it is not.
    AgentEventKind.BROWSER_SESSION.value: INCLUDED,
    AgentEventKind.MEMORY_LEARNED.value: INCLUDED,
    AgentEventKind.ROUTINE_FIRED.value: INCLUDED,
    AgentEventKind.ROUTINE_SKIPPED.value: INCLUDED,
    AgentEventKind.DELIVERABLE.value: ROUTINE_RUN,
    AgentEventKind.COULD_NOT.value: INCLUDED,
    AgentEventKind.MESSAGE.value: TEXT_REPLY,
}


def credits_for(event: str) -> int:
    # The builder-message fee is retired by the 21 September 2026 ladder: a
    # builder call costs the model rate from the same allowance and nothing
    # on top. One counter is easier to explain than two.
    if event == BUILDER_MESSAGE and constants.PLAN_LADDER_2026_09_ENABLED:
        return 0
    # D-1: with the charge rule on, the exchange table is the price list.
    # ``EVENT_CREDITS`` stays the table the flag-off world charges from.
    from api.services.billing import exchange

    if exchange.enabled() and event in exchange.CREDITS:
        return exchange.CREDITS[event]
    return EVENT_CREDITS[event]


def is_metered(event: str) -> bool:
    """Whether ``charge`` accepts this event. Transcription is metered only
    while the charge rule is on."""
    from api.services.billing import exchange

    return event in EVENT_CREDITS or (exchange.enabled() and event == TRANSCRIPTION)


def paise_for(event: str, quantity: int = 1) -> int:
    return credits_for(event) * PAISE_PER_CREDIT * max(1, int(quantity))


def tool_call_event(toolkit: str | None, action: str | None = None) -> str:
    """Which tool-call rate a connector is billed at.

    Off the charge rule, the connector alone decides. On it (D-1), only a
    *write* into a system of record is premium: ``action`` is the action or
    tool name, classified by ``exchange.is_write_action``; a read, or a name
    that says neither, is an ordinary call.
    """
    from api.services.billing import exchange

    premium = (toolkit or "").strip().lower() in PREMIUM_CONNECTORS
    if premium and exchange.enabled():
        premium = exchange.is_write_action(action)
    return TOOL_CALL_PREMIUM if premium else TOOL_CALL


def turn_used_knowledge(turn: dict | None) -> bool:
    """Whether a completed text-chat turn called knowledge retrieval."""
    for event in (turn or {}).get("events") or []:
        if not isinstance(event, dict) or event.get("type") != "tool_call_started":
            continue
        payload = event.get("payload") or {}
        if payload.get("function_name") == KNOWLEDGE_TOOL_NAME:
            return True
    return False


def turn_found_knowledge(turn: dict | None) -> bool:
    """Whether retrieval on this turn actually found something.

    A knowledge answer is 2 credits; when the documents had nothing on it the
    reply is billed as a plain reply, 1 credit (KAN-47, 14 Sept). The tool
    reports ``status`` on its result (``ok``, ``no_match``, ``unavailable``),
    and the runner records that result on the turn. A turn with a call and
    no recorded result (an older session) is taken as found, which is the
    price the customer was always charged.
    """
    started = False
    for event in (turn or {}).get("events") or []:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") or {}
        if payload.get("function_name") != KNOWLEDGE_TOOL_NAME:
            continue
        if event.get("type") == "tool_call_started":
            started = True
        elif event.get("type") == "tool_call_result":
            result = payload.get("result")
            if isinstance(result, dict):
                return result.get("status", "ok") == "ok"
    return started


def event_for_turn(turn: dict | None) -> str:
    if not turn_used_knowledge(turn):
        return TEXT_REPLY
    return KNOWLEDGE_ANSWER if turn_found_knowledge(turn) else TEXT_REPLY


def translation_quantity(text: str | None) -> int:
    """How many 100-character units a translation is billed as, minimum 1."""
    chars = len(text or "")
    return max(1, -(-chars // TRANSLATION_CHARS_PER_CREDIT))


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


def turn_usages(text_session, *, last_only: bool = False) -> list[dict]:
    """The model usage each completed turn of a text session recorded, for
    the model line (D-1). ``last_only`` for an event that is one turn of a
    longer session (a share-link reply); otherwise every turn of the run,
    which is what a channel reply, a trigger, a task or a routine is.
    Defensive for the same reason as ``last_turn_of``."""
    data = getattr(text_session, "session_data", None)
    if not isinstance(data, dict):
        return []
    turns = data.get("turns")
    if not isinstance(turns, list):
        return []
    if last_only:
        turns = turns[-1:]
    return [
        turn["usage"]
        for turn in turns
        if isinstance(turn, dict) and isinstance(turn.get("usage"), dict)
    ]


def stamped_credits(event: str, charged_paise: int) -> int:
    """What a reply's timeline row says it cost. The event's figure, or --
    under the charge rule, when a model line was added -- what the ledger
    row actually took."""
    from api.services.billing import exchange

    figure = credits_for(event)
    if exchange.enabled() and charged_paise > figure * PAISE_PER_CREDIT:
        return charged_paise // PAISE_PER_CREDIT
    return figure


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
    from api.services.billing import exchange

    if exchange.enabled():
        return {"credits": credits_for(price), "included": False}
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
    workflow_id: int | None = None,
    usage: list[dict | None] | None = None,
    credits: int | None = None,
) -> int:
    """Debit one event. Returns the paise debited (0 if already done, or the
    account is internal).

    With the charge rule on (D-1), ``usage`` is the model usage the event's
    turns recorded; a text event includes the standard-model allowance and
    anything past it, or any premium model's tokens, is added to this same
    row as extra credits. ``credits`` prices an event measured rather than
    counted (a transcription's minutes). Both are ignored with the rule off.

    Keyed on ``(event, ref_id)``: a retried task, a redelivered job, a
    handler that ran twice — all find the row and write nothing. The ref is
    the event's own id (a run, a turn, a tool call), never a timestamp.

    ``workflow_id`` is the bot that did the work, stamped on the ledger row
    so its spend can be capped (S-1). None for an event nobody's agent made.
    """
    from api.services.billing import exchange
    from api.services.billing.internal_accounts import is_internal

    if not is_metered(event):
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
    rule = exchange.enabled()
    if rule and credits is not None:
        amount = max(0, int(credits)) * PAISE_PER_CREDIT
    model_credits = 0
    if rule and usage and event in exchange.TOKEN_EVENTS:
        model_credits = await exchange.model_credits(
            session, usage, event_credits=amount // PAISE_PER_CREDIT
        )
    # Under the organisation's ledger lock (KAN-44): the balance read and the
    # row that records balance_after_paise happen with nothing in between.
    from api.services.billing.ledger_lock import lock_organization_ledger

    await lock_organization_ledger(session, organization_id=organization_id)
    balance = await _balance_paise(session, organization_id=organization_id)
    event_credits = amount // PAISE_PER_CREDIT
    label = EVENT_LABELS[event]
    if quantity > 1:
        label += f" ×{quantity}"
    priced = f"{event_credits} credit{'s' if event_credits != 1 else ''}"
    if model_credits:
        # One row, one rounding: the event and its model line together.
        priced += f" + {model_credits} model"
        amount += model_credits * PAISE_PER_CREDIT
    session.add(
        CreditLedgerModel(
            organization_id=organization_id,
            delta_paise=-amount,
            kind=CreditLedgerKind.USAGE.value,
            ref_type=event,
            ref_id=ref_id[:64],
            balance_after_paise=balance - amount,
            note=f"{label} · {priced}" + (f" · {note}" if note else ""),
            created_at=datetime.now(UTC),
            workflow_id=workflow_id,
        )
    )
    await session.flush()
    from api.services.billing import budgets

    await budgets.observe_charge(
        session, organization_id=organization_id, workflow_id=workflow_id
    )
    return amount


async def charge_transcription(
    *,
    organization_id: int | None,
    ref_id: str,
    seconds: float | int | None,
    note: str | None = None,
) -> int:
    """Charge one transcribed file (D-1): 2 credits a minute, rounded up per
    file, 1 credit minimum. Nothing while the charge rule is off -- the
    transcription was measured, never billed, before it. Never raises."""
    from api.services.billing import exchange

    if not exchange.enabled():
        return 0
    return await charge_in_own_session(
        organization_id=organization_id,
        event=TRANSCRIPTION,
        ref_id=ref_id,
        credits=exchange.transcription_credits(seconds),
        note=note,
    )


async def charge_in_own_session(
    *,
    organization_id: int | None,
    event: str,
    ref_id: str,
    quantity: int = 1,
    note: str | None = None,
    workflow_id: int | None = None,
    usage: list[dict | None] | None = None,
    credits: int | None = None,
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
                workflow_id=workflow_id,
                usage=usage,
                credits=credits,
            )
            await session.commit()
            return amount
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.error(
            "Could not charge {} {} for org {}: {}", event, ref_id, organization_id, exc
        )
        return 0
