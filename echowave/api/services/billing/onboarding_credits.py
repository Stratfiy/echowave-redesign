"""Free credit, earned one step at a time (KAN-132).

A new account used to get its whole free allowance the moment its address was
proved. Now the Free 1,000 credits arrive in six tranches, each unlocked by
doing the thing the product is for: prove the address, build a bot, put it on
a channel, have a real conversation, let a routine run on its own, move in.

Why tranches rather than a lump. Each step costs the claimant something a
script cannot fake cheaply, so bonus farming stops paying. Each step is also
one of the activation events the KPI board measures, so onboarding and the
funnel are the same list. And a person who has just heard their bot take a
call is far more likely to schedule a routine than one who has not, so the
order teaches the product in the order it earns trust.

**Derived, not event-driven.** Whether a step is done is read from the
database each time (is there a workflow? a completed call? a fired
routine?), never from a flag set by the code path that did it. A missed hook
would otherwise mean a step that quietly never pays, which nobody would
notice until a customer asked. ``settle`` is called from a few natural
moments and, above all, every time the Home screen loads, so it always
catches up.

**Still a gift.** Every tranche lands as a :attr:`CreditLedgerKind.TRIAL`
row: no GST, no receipt voucher, and revenue reporting can exclude it
forever. Once per step per organisation, enforced by a partial unique index
rather than a check in application code, because two requests can race here
exactly as they can at signup. The first step keeps the historical
``signup_bonus`` ref so accounts that already received it are not paid twice.

**A ceiling, not a floor.** The steps sum to the Free allowance, and an
account that already holds trial credit from before this change keeps it:
each tranche is clipped so the trial total never passes the allowance.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.models import (
    AgentEventModel,
    CreditLedgerModel,
    KnowledgeBaseDocumentModel,
    OrganizationInvitationModel,
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import (
    AgentEventKind,
    CreditLedgerKind,
    PostHogEvent,
    WorkflowRunMode,
    WorkflowRunState,
)
from api.services.posthog_client import capture_event

#: ₹0.50 a credit (KAN-52).
PAISE_PER_CREDIT = 50

#: The Free allowance, in credits (KAN-47). The steps below sum to it.
FREE_CREDITS = 1_000

#: Switch the whole scheme off (``false``); a deployment with no free tier.
ENABLED = os.getenv("ONBOARDING_CREDITS_ENABLED", "true").lower() not in (
    "0",
    "false",
    "no",
)

#: Steps held back without a release, by key: ``ONBOARDING_STEPS_DISABLED=
#: first_routine`` while the routines runtime is still settling.
DISABLED_STEPS: frozenset[str] = frozenset(
    s.strip()
    for s in os.getenv("ONBOARDING_STEPS_DISABLED", "").split(",")
    if s.strip()
)

VERIFY_EMAIL = "verify_email"
FIRST_BOT = "first_bot"
FIRST_CHANNEL = "first_channel"
FIRST_CONVERSATION = "first_conversation"
FIRST_ROUTINE = "first_routine"
MOVED_IN = "moved_in"

#: The ref the original one-shot bonus used. Kept for the first step so an
#: account that already has it is recognised as done, and its unique index
#: keeps doing the once-only work it always did.
LEGACY_REF_TYPE = "signup_bonus"

REF_PREFIX = "onboarding:"

#: Test and browser modes: a conversation with yourself is not a customer.
_REHEARSAL_MODES = (
    WorkflowRunMode.WEBRTC.value,
    WorkflowRunMode.SMALLWEBRTC.value,
    WorkflowRunMode.TEXTCHAT.value,
)

Check = Callable[[AsyncSession, int], Awaitable[bool]]


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    credits: int
    #: One line on the checklist saying what to do.
    hint: str
    #: Where the checklist sends them to do it.
    href: str
    check: Check

    @property
    def ref_type(self) -> str:
        return LEGACY_REF_TYPE if self.key == VERIFY_EMAIL else REF_PREFIX + self.key

    @property
    def paise(self) -> int:
        return self.credits * PAISE_PER_CREDIT

    @property
    def enabled(self) -> bool:
        return self.key not in DISABLED_STEPS


# ---------------------------------------------------------------------------
# What "done" means for each step, read from the database
# ---------------------------------------------------------------------------


async def _email_verified(session: AsyncSession, organization_id: int) -> bool:
    from api.services.auth.email_verification import verification_is_enforceable

    # Google and Stack vouch for the address; a deployment without mail has no
    # code to send. There the step is done on arrival, as the bonus always was.
    if not verification_is_enforceable():
        return True
    return (
        await session.scalar(
            select(UserModel.id)
            .join(
                OrganizationMembershipModel,
                OrganizationMembershipModel.user_id == UserModel.id,
            )
            .where(
                OrganizationMembershipModel.organization_id == organization_id,
                UserModel.email_verified_at.isnot(None),
            )
            .limit(1)
        )
        is not None
    )


async def _has_a_bot(session: AsyncSession, organization_id: int) -> bool:
    return (
        await session.scalar(
            select(WorkflowModel.id)
            .where(WorkflowModel.organization_id == organization_id)
            .limit(1)
        )
        is not None
    )


def _text_reply_rows(organization_id: int):
    from api.services.billing import events

    return select(CreditLedgerModel.id).where(
        CreditLedgerModel.organization_id == organization_id,
        (CreditLedgerModel.kind == CreditLedgerKind.MESSAGE.value)
        | (CreditLedgerModel.ref_type == events.TEXT_REPLY),
    )


async def _on_a_channel(session: AsyncSession, organization_id: int) -> bool:
    """A message went out or a reply came back on WhatsApp, email or web chat.
    The ledger is the record of it; a bot merely configured for a channel has
    not been put on it until something is said."""
    return await session.scalar(_text_reply_rows(organization_id).limit(1)) is not None


async def _had_a_conversation(session: AsyncSession, organization_id: int) -> bool:
    """A finished call that was not a rehearsal, or a real message exchange."""
    call = await session.scalar(
        select(WorkflowRunModel.id)
        .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
        .where(
            WorkflowModel.organization_id == organization_id,
            WorkflowRunModel.state == WorkflowRunState.COMPLETED.value,
            WorkflowRunModel.mode.notin_(_REHEARSAL_MODES),
            func.coalesce(WorkflowRunModel.billable_seconds, 0) > 0,
        )
        .limit(1)
    )
    if call is not None:
        return True
    return await _on_a_channel(session, organization_id)


async def _routine_fired(session: AsyncSession, organization_id: int) -> bool:
    return (
        await session.scalar(
            select(AgentEventModel.id)
            .where(
                AgentEventModel.organization_id == organization_id,
                AgentEventModel.kind == AgentEventKind.ROUTINE_FIRED.value,
            )
            .limit(1)
        )
        is not None
    )


async def _moved_in(session: AsyncSession, organization_id: int) -> bool:
    document = await session.scalar(
        select(KnowledgeBaseDocumentModel.id)
        .where(
            KnowledgeBaseDocumentModel.organization_id == organization_id,
            KnowledgeBaseDocumentModel.processing_status == "completed",
        )
        .limit(1)
    )
    if document is not None:
        return True
    return (
        await session.scalar(
            select(OrganizationInvitationModel.id)
            .where(
                OrganizationInvitationModel.organization_id == organization_id,
                OrganizationInvitationModel.accepted_at.isnot(None),
            )
            .limit(1)
        )
        is not None
    )


STEPS: tuple[Step, ...] = (
    Step(
        VERIFY_EMAIL,
        "Verify your email",
        150,
        "Enter the six-digit code we sent you.",
        "/overview",
        _email_verified,
    ),
    Step(
        FIRST_BOT,
        "Build your first bot",
        150,
        "Describe the job in the box above, or pick one from the marketplace.",
        "/start",
        _has_a_bot,
    ),
    Step(
        FIRST_CHANNEL,
        "Put it on a channel",
        150,
        "Send or answer a first message on WhatsApp, email or web chat.",
        "/marketplace",
        _on_a_channel,
    ),
    Step(
        FIRST_CONVERSATION,
        "Have a real conversation",
        200,
        "A call or a message exchange with somebody other than you.",
        "/workflow",
        _had_a_conversation,
    ),
    Step(
        FIRST_ROUTINE,
        "Schedule a routine, and let it run",
        200,
        "Credit lands when it fires for the first time. That first run is free.",
        "/workflow",
        _routine_fired,
    ),
    Step(
        MOVED_IN,
        "Move in",
        150,
        "Upload a document to your knowledge base, or invite a teammate.",
        "/knowledge-base",
        _moved_in,
    ),
)

STEPS_BY_KEY: dict[str, Step] = {step.key: step for step in STEPS}

assert sum(step.credits for step in STEPS) == FREE_CREDITS, "the steps must sum to Free"

#: The welcome grant under the 21 September 2026 ladder: 100 credits, the
#: same six steps, and the unspent part expires thirty days after it was
#: granted (``expire_welcome_grants``). Today's 1,000 is ₹500 of exposure per
#: signup, most of which is never earned; 100 is enough for one real agent and
#: a routine, which is what the step list already asks for.
FREE_CREDITS_2026_09 = 100
WELCOME_GRANT_DAYS = 30
_STEP_CREDITS_2026_09: dict[str, int] = {
    VERIFY_EMAIL: 20,
    FIRST_BOT: 20,
    FIRST_CHANNEL: 15,
    FIRST_CONVERSATION: 20,
    FIRST_ROUTINE: 15,
    MOVED_IN: 10,
}


def _resized(step: Step, credits: int) -> Step:
    try:
        import dataclasses

        return dataclasses.replace(step, credits=credits)
    except TypeError:
        return step._replace(credits=credits)  # type: ignore[attr-defined]


STEPS_2026_09: tuple[Step, ...] = tuple(
    _resized(step, _STEP_CREDITS_2026_09[step.key]) for step in STEPS
)
STEPS_BY_KEY_2026_09: dict[str, Step] = {step.key: step for step in STEPS_2026_09}

assert sum(step.credits for step in STEPS_2026_09) == FREE_CREDITS_2026_09


def free_credits() -> int:
    """The welcome grant in force, read at call time."""
    return (
        FREE_CREDITS_2026_09 if constants.PLAN_LADDER_2026_09_ENABLED else FREE_CREDITS
    )


def steps() -> tuple[Step, ...]:
    return STEPS_2026_09 if constants.PLAN_LADDER_2026_09_ENABLED else STEPS


def steps_by_key() -> dict[str, Step]:
    return (
        STEPS_BY_KEY_2026_09 if constants.PLAN_LADDER_2026_09_ENABLED else STEPS_BY_KEY
    )


REF_TYPE_WELCOME_EXPIRY = "welcome_expiry"


async def expire_welcome_grants(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, int]:
    """Retire the unspent part of welcome grants older than thirty days.

    Only under the 21 September 2026 ladder; the 1,000-credit grant it
    replaces never expired and keeps not expiring. Written as the plan-expiry
    ledger kind with its own ref type, so the statement says "this expired"
    rather than showing an unexplained debit.

    Per account, once: every welcome step row older than the cutoff is taken
    together, what was spent since the oldest of them is subtracted (usage
    draws the welcome balance down first, as it does a plan's), and the
    remainder is clamped to the balance actually on the account — a top-up
    on top of a welcome grant keeps every rupee of the top-up.
    """
    counters = {"considered": 0, "expired": 0, "paise": 0}
    if not constants.PLAN_LADDER_2026_09_ENABLED:
        return counters
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=WELCOME_GRANT_DAYS)

    from sqlalchemy import select

    from api.services.billing.costing import current_balance_paise
    from api.services.billing.ledger_lock import lock_organization_ledger
    from api.services.billing.plans import _consumed_since

    aged = (
        (
            await session.execute(
                select(CreditLedgerModel)
                .where(
                    CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
                    CreditLedgerModel.created_at < cutoff,
                )
                .order_by(CreditLedgerModel.organization_id, CreditLedgerModel.id)
            )
        )
        .scalars()
        .all()
    )
    by_org: dict[int, list[CreditLedgerModel]] = {}
    for row in aged:
        by_org.setdefault(int(row.organization_id), []).append(row)

    for organization_id, rows in by_org.items():
        counters["considered"] += 1
        ids = {str(r.id) for r in rows}
        already = await session.scalar(
            select(CreditLedgerModel.id).where(
                CreditLedgerModel.organization_id == organization_id,
                CreditLedgerModel.kind == CreditLedgerKind.PLAN_EXPIRY.value,
                CreditLedgerModel.ref_type == REF_TYPE_WELCOME_EXPIRY,
                CreditLedgerModel.ref_id.in_(ids),
            )
        )
        if already is not None:
            continue
        await lock_organization_ledger(session, organization_id=organization_id)
        oldest = rows[0]
        granted = sum(int(r.delta_paise) for r in rows)
        consumed = await _consumed_since(session, grant=oldest)
        balance = await current_balance_paise(session, organization_id=organization_id)
        remaining = max(0, min(granted - consumed, balance))
        newest = rows[-1]
        session.add(
            CreditLedgerModel(
                organization_id=organization_id,
                delta_paise=-remaining,
                kind=CreditLedgerKind.PLAN_EXPIRY.value,
                ref_type=REF_TYPE_WELCOME_EXPIRY,
                ref_id=str(newest.id),
                balance_after_paise=balance - remaining,
                note=(
                    f"Welcome credits expired after {WELCOME_GRANT_DAYS} days "
                    f"({remaining // PAISE_PER_CREDIT} credits unused)"
                ),
            )
        )
        await session.flush()
        if remaining:
            counters["expired"] += 1
            counters["paise"] += remaining
    return counters


# ---------------------------------------------------------------------------
# Granting
# ---------------------------------------------------------------------------


async def _trial_granted_paise(session: AsyncSession, organization_id: int) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
                CreditLedgerModel.organization_id == organization_id,
                CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
            )
        )
        or 0
    )


async def _granted_by_step(
    session: AsyncSession, organization_id: int
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(CreditLedgerModel.ref_type, CreditLedgerModel.delta_paise).where(
                CreditLedgerModel.organization_id == organization_id,
                CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
                CreditLedgerModel.ref_type.in_([step.ref_type for step in steps()]),
            )
        )
    ).all()
    by_ref = {str(ref): int(paise) for ref, paise in rows}
    return {
        step.key: by_ref[step.ref_type] for step in steps() if step.ref_type in by_ref
    }


async def _is_internal(session: AsyncSession, organization_id: int) -> bool:
    return bool(
        await session.scalar(
            select(OrganizationModel.internal_billing).where(
                OrganizationModel.id == organization_id
            )
        )
    )


async def grant_step(session: AsyncSession, *, organization_id: int, key: str) -> int:
    """Pay one step's tranche if it is not already paid. Returns the paise.

    Does not check whether the step is *done* — ``settle`` does that; this
    is the write, callable directly by the verify route where "done" is the
    thing that just happened. Clipped to the Free ceiling, once-only by the
    unique index, and a lost race is a quiet 0: the other request paid it.

    The insert runs in a savepoint so a caller mid-transaction (the costing
    hook) keeps its own work if the row already exists.
    """
    step = steps_by_key()[key]
    if not ENABLED or not step.enabled:
        return 0
    if await _is_internal(session, organization_id):
        return 0
    if key in await _granted_by_step(session, organization_id):
        return 0

    already = await _trial_granted_paise(session, organization_id)
    amount = min(step.paise, free_credits() * PAISE_PER_CREDIT - already)
    if amount <= 0:
        return 0

    from api.services.billing.costing import current_balance_paise
    from api.services.billing.ledger_lock import lock_organization_ledger

    await lock_organization_ledger(session, organization_id=organization_id)
    balance = await current_balance_paise(session, organization_id=organization_id)
    try:
        async with session.begin_nested():
            session.add(
                CreditLedgerModel(
                    organization_id=organization_id,
                    delta_paise=amount,
                    kind=CreditLedgerKind.TRIAL.value,
                    ref_type=step.ref_type,
                    ref_id=str(organization_id),
                    balance_after_paise=balance + amount,
                    note=f"Onboarding: {step.label.lower()} "
                    f"({amount // PAISE_PER_CREDIT} credits)",
                )
            )
            await session.flush()
    except IntegrityError:
        logger.debug(
            "Onboarding step {} for org {} was paid concurrently", key, organization_id
        )
        return 0

    logger.info(
        "Org {} earned {} credits for onboarding step {}",
        organization_id,
        amount // PAISE_PER_CREDIT,
        key,
    )
    from api.services.notifications import inbox

    await inbox.post(
        organization_id=organization_id,
        kind="onboarding_credits",
        dedupe_key=f"{organization_id}:{key}",
        title=f"{amount // PAISE_PER_CREDIT} free credits: {step.label.lower()}",
        body=_next_hint(key),
        link="/overview",
    )
    capture_event(
        distinct_id=str(organization_id),
        event=PostHogEvent.SIGNUP_BONUS_GRANTED,
        properties={
            "organization_id": organization_id,
            "amount_paise": amount,
            "step": key,
        },
    )
    return amount


def _next_hint(key: str) -> str:
    keys = [step.key for step in steps()]
    after = keys[keys.index(key) + 1 :]
    for candidate in after:
        step = steps_by_key()[candidate]
        if step.enabled:
            return f"Next: {step.label.lower()} for {step.credits} more."
    return "That is every free credit earned. Thank you for moving in."


async def settle(session: AsyncSession, *, organization_id: int) -> list[str]:
    """Pay every step that is done and not yet paid. Returns the keys paid."""
    if not ENABLED or await _is_internal(session, organization_id):
        return []
    paid = await _granted_by_step(session, organization_id)
    granted: list[str] = []
    for step in steps():
        if not step.enabled or step.key in paid:
            continue
        if await step.check(session, organization_id) and await grant_step(
            session, organization_id=organization_id, key=step.key
        ):
            granted.append(step.key)
    return granted


async def settle_in_own_session(organization_id: int | None) -> list[str]:
    """``settle`` from a task or a route that holds no session. Never raises:
    a ledger problem must not fail the routine or the call that earned it."""
    if organization_id is None:
        return []
    from api.db import db_client

    try:
        async with db_client.async_session() as session:
            granted = await settle(session, organization_id=organization_id)
            if granted:
                await session.commit()
            return granted
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.error(
            "Could not settle onboarding credits for org {}: {}", organization_id, exc
        )
        return []


# ---------------------------------------------------------------------------
# The checklist
# ---------------------------------------------------------------------------


async def state(session: AsyncSession, *, organization_id: int) -> dict:
    """The six steps as the Home screen shows them."""
    paid = await _granted_by_step(session, organization_id)
    listed = []
    for step in steps():
        done = step.key in paid or await step.check(session, organization_id)
        listed.append(
            {
                "key": step.key,
                "label": step.label,
                "hint": step.hint,
                "href": step.href,
                "credits": step.credits,
                "done": done,
                "paid": step.key in paid,
                "granted_credits": paid.get(step.key, 0) // PAISE_PER_CREDIT,
                "enabled": step.enabled,
            }
        )
    granted = sum(paid.values()) // PAISE_PER_CREDIT
    return {
        "enabled": ENABLED,
        "free_credits": free_credits(),
        "granted_credits": granted,
        "remaining_credits": max(free_credits() - granted, 0),
        "complete": all(s["done"] or not s["enabled"] for s in listed),
        "steps": listed,
    }


async def first_routine_run_is_free(
    session: AsyncSession, *, organization_id: int
) -> bool:
    """The routine step's promise: the first run is on the house.

    Decided by counting ``routine_fired`` events, which the scheduler writes
    before it enqueues the run. The first run sees exactly one, every later
    run sees more, and a re-fired job sees the same count it saw before.
    """
    if not ENABLED or not steps_by_key()[FIRST_ROUTINE].enabled:
        return False
    if await _is_internal(session, organization_id):
        return False
    fired = await session.scalar(
        select(func.count(AgentEventModel.id)).where(
            AgentEventModel.organization_id == organization_id,
            AgentEventModel.kind == AgentEventKind.ROUTINE_FIRED.value,
        )
    )
    return int(fired or 0) <= 1
