"""Autopay for the monthly number rental — the standing authorisation itself.

A number rental collected out of a prepaid balance stops the moment the balance
does, and what is left is a number we keep paying a carrier for with nothing
standing behind the rent. A mandate inverts that: the collection is pushed to
the customer's bank on a schedule rather than pulled from a balance they may
have forgotten to top up.

This module owns the authorisation, not the money. It creates the subscription,
tracks its state from webhooks, and answers one question for the provisioning
path — *may we hand this account a number?* The collection that happens under it
is recorded by :mod:`api.services.billing.rentals`.

Four rules, each of which exists because breaking it costs real money:

1. **Only a signature-verified webhook changes a mandate's state.** The state is
   the difference between a number we can bill for and one we cannot, and the
   browser saying "I authorised it" is an unauthenticated client asserting that.
   Verification happens in :mod:`api.services.billing.payments`, which owns the
   secret; this module is only ever handed an already-verified event.
2. **A number waits on the mandate, never the other way round.** A number issued
   against an unauthorised mandate is a number we pay for and cannot collect on.
3. **A mandate collection does not also debit the prepaid balance.** Two
   collection paths for one month is a double charge, and it is the kind that
   looks correct in both ledgers separately.
4. **Losing a mandate is not losing the money.** A revoked or halted mandate
   leaves the rental in place and falls back to the balance and the dunning
   schedule — that is precisely the situation dunning was written for.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import (
    NUMBER_RENTAL_PRICE_PAISE,
    RAZORPAY_API_BASE,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_RENTAL_PLAN_ID,
    RAZORPAY_STARTER_PLAN_ID,
    REQUIRE_MANDATE_FOR_NUMBERS,
    STARTER_PLAN_PRICE_PAISE,
)
from api.db.models import PaymentMandateModel
from api.enums import MandateStatus
from api.services.billing.billing_profile import get_profile
from api.services.billing.tax import TaxError, gross_up

PROVIDER = "razorpay"
PURPOSE_NUMBER_RENTAL = "number_rental"

#: The starter plan: one monthly collection covering a number's rent *and* a
#: call balance. A second purpose rather than a variant of the first, because
#: the two authorise different amounts and an account holding both would be
#: paying for the same number twice.
PURPOSE_STARTER_PLAN = "starter_plan"

#: Purposes that entitle an account to hold a number, in the order they are
#: preferred. The plan comes first: an account on the plan has already
#: authorised enough to cover the rent, so sending it to take out a second
#: rental mandate would ask for a second standing instruction covering a charge
#: the first one already includes.
NUMBER_BEARING_PURPOSES = (PURPOSE_STARTER_PLAN, PURPOSE_NUMBER_RENTAL)

#: How many billing cycles a rental mandate authorises. Razorpay requires a
#: finite count, so this is ten years of monthly collection — long enough that
#: nobody hits it, short enough that an abandoned mandate does not outlive the
#: company.
RENTAL_TOTAL_COUNT = 120

_TIMEOUT = httpx.Timeout(20.0)

#: Provider event -> the state it puts a mandate in. Written as a table rather
#: than a chain of ifs because the set of events is the provider's, not ours,
#: and an unmapped event must be visibly unmapped rather than silently treated
#: as "no change".
_EVENT_STATUS = {
    "subscription.authenticated": MandateStatus.AUTHENTICATED.value,
    "subscription.activated": MandateStatus.ACTIVE.value,
    "subscription.charged": MandateStatus.ACTIVE.value,
    "subscription.pending": MandateStatus.PENDING.value,
    "subscription.halted": MandateStatus.HALTED.value,
    "subscription.cancelled": MandateStatus.CANCELLED.value,
    "subscription.completed": MandateStatus.COMPLETED.value,
    "subscription.paused": MandateStatus.HALTED.value,
    "subscription.resumed": MandateStatus.ACTIVE.value,
    "subscription.updated": None,  # Recognised; carries no state transition.
}

#: Terminal states. A mandate in one of these is never revived — the customer
#: authorises a new one, which is also what the provider requires.
TERMINAL = frozenset(
    {
        MandateStatus.CANCELLED.value,
        MandateStatus.COMPLETED.value,
        MandateStatus.EXPIRED.value,
    }
)


class MandateError(RuntimeError):
    """The mandate could not be created or updated."""


class MandateNotConfigured(MandateError):
    """Razorpay Subscriptions is not set up on this deployment.

    Separate from MandateError because it is an operator problem, not a
    customer one, and the route says so rather than reporting a payment
    failure.
    """


class MandateNotAuthorised(PermissionError):
    """The account has no authorised mandate, so no number may be issued.

    A PermissionError rather than a MandateError: like the verification gate it
    sits beside, this is the customer's to fix and the route turns it into a 403
    carrying the sentence that tells them how.
    """


def is_configured() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def _auth() -> tuple[str, str]:
    if not is_configured():
        raise MandateNotConfigured(
            "Razorpay is not configured, so autopay cannot be set up. Set "
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
        )
    return RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET  # type: ignore[return-value]


async def _post(path: str, payload: dict, *, client: httpx.AsyncClient | None = None):
    owned = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        response = await client.post(
            f"{RAZORPAY_API_BASE}{path}", json=payload, auth=_auth()
        )
    except httpx.HTTPError as exc:
        raise MandateError(f"Razorpay request failed: {exc}") from exc
    finally:
        if owned:
            await client.aclose()

    if response.status_code >= 400:
        # The provider's own description, not ours. "Subscriptions is not
        # enabled for this account" is the single most likely failure here and
        # is far more useful than a status code.
        detail = ""
        try:
            detail = (response.json().get("error") or {}).get("description") or ""
        except ValueError:
            detail = response.text[:200]
        raise MandateError(
            f"Razorpay returned {response.status_code} for {path}: {detail}"
        )
    return response.json()


async def _ensure_plan(
    *,
    pinned: str | None,
    env_var: str,
    name: str,
    description: str,
    price_paise: int,
    client: httpx.AsyncClient | None = None,
) -> str:
    """The provider plan id a subscription is created against.

    Pinning the id in the environment is strongly preferred: a plan created
    lazily per environment fragments the provider's own reporting, and the
    fragments are indistinguishable after the fact.

    ``price_paise`` is the **gross** figure the bank is told to collect. The
    caller grosses up, because the rate depends on the account's own billing
    profile and this function has no view of one.
    """
    if pinned:
        return pinned

    created = await _post(
        "/plans",
        {
            "period": "monthly",
            "interval": 1,
            "item": {
                "name": name,
                "amount": int(price_paise),
                "currency": "INR",
                "description": description,
            },
        },
        client=client,
    )
    plan_id = created.get("id")
    if not plan_id:
        raise MandateError("Razorpay created a plan with no id")
    logger.warning(
        "Created Razorpay plan {} on the fly; pin it as {} so later "
        "environments reuse it",
        plan_id,
        env_var,
    )
    return plan_id


async def ensure_rental_plan(
    *, price_paise: int, client: httpx.AsyncClient | None = None
) -> str:
    """The plan id a number-rental subscription is created against."""
    return await _ensure_plan(
        pinned=RAZORPAY_RENTAL_PLAN_ID,
        env_var="RAZORPAY_RENTAL_PLAN_ID",
        name="Decibyl phone number",
        description="Monthly rental for a Decibyl phone number",
        price_paise=price_paise,
        client=client,
    )


async def ensure_starter_plan(
    *, price_paise: int, client: httpx.AsyncClient | None = None
) -> str:
    """The plan id a starter-plan subscription is created against."""
    return await _ensure_plan(
        pinned=RAZORPAY_STARTER_PLAN_ID,
        env_var="RAZORPAY_STARTER_PLAN_ID",
        name="Decibyl starter plan",
        description="Monthly: a phone number and a call balance",
        price_paise=price_paise,
        client=client,
    )


async def get_mandate(
    session: AsyncSession,
    *,
    organization_id: int,
    purpose: str = PURPOSE_NUMBER_RENTAL,
) -> PaymentMandateModel | None:
    """This account's live mandate, in whatever state it is in.

    Returns the non-terminal one — there is at most one, enforced by a partial
    unique index rather than by this query, so a race cannot produce two.
    """
    return await session.scalar(
        select(PaymentMandateModel)
        .where(
            PaymentMandateModel.organization_id == organization_id,
            PaymentMandateModel.purpose == purpose,
            PaymentMandateModel.status.not_in(tuple(TERMINAL)),
        )
        .order_by(PaymentMandateModel.id.desc())
    )


def is_authorised(mandate: PaymentMandateModel | None) -> bool:
    return bool(mandate and mandate.status in MandateStatus.authorised())


async def create_rental_mandate(
    session: AsyncSession,
    *,
    organization_id: int,
    price_paise: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> PaymentMandateModel:
    """Start the autopay authorisation for a number rental.

    Returns a row carrying ``short_url`` — the provider-hosted page where the
    customer actually authorises. Nothing is collected here and no number is
    issued here; the mandate becomes usable only when the provider tells us it
    was authorised.

    Calling this twice returns the existing live mandate rather than creating a
    second one. A customer who abandons the authorisation page and comes back
    must not end up with two banks collecting for the same number.
    """
    existing = await get_mandate(session, organization_id=organization_id)
    if existing is not None:
        return existing

    # NUMBER_RENTAL_PRICE_PAISE is a **net** figure everywhere else: the
    # prepaid path debits it from the credit ledger, which is defined as
    # GST-exclusive. Creating the plan at that number told the bank to collect
    # the net amount, so every autopay rental collected no GST at all while the
    # same rental billed prepaid collected it — two paths disagreeing about
    # what ₹349 means, with the difference coming out of margin.
    net_paise = int(price_paise or NUMBER_RENTAL_PRICE_PAISE)
    profile = await get_profile(session, organization_id=organization_id)
    try:
        gross_paise = gross_up(
            taxable_paise=net_paise,
            country_code=profile.country_code,
            state_code=profile.state_code,
        )
    except TaxError as exc:
        # An export with no LUT on file. Surfaced rather than swallowed: it is
        # fixed by filing the LUT, not by collecting an amount we cannot
        # invoice, month after month, by standing instruction.
        raise MandateError(str(exc)) from exc

    plan_id = await ensure_rental_plan(price_paise=gross_paise, client=client)

    created = await _post(
        "/subscriptions",
        {
            "plan_id": plan_id,
            "total_count": RENTAL_TOTAL_COUNT,
            # The customer is charged when the first cycle falls due, not on
            # authorisation. The rental's own prorated first period is billed by
            # the rental code, so collecting here as well would take the first
            # month twice.
            "customer_notify": 1,
            "notes": {
                "organization_id": str(organization_id),
                "purpose": "number_rental",
            },
        },
        client=client,
    )

    mandate = PaymentMandateModel(
        organization_id=organization_id,
        provider=PROVIDER,
        purpose=PURPOSE_NUMBER_RENTAL,
        subscription_id=created.get("id"),
        plan_id=plan_id,
        customer_id=created.get("customer_id"),
        status=created.get("status") or MandateStatus.CREATED.value,
        short_url=created.get("short_url"),
        price_paise=price_paise,
        provider_payload=created,
    )
    session.add(mandate)
    try:
        await session.flush()
    except IntegrityError:
        # Two requests raced onto the partial unique index. The other one won;
        # its row is the truth. The subscription we just created at the provider
        # is orphaned and is logged so it can be cancelled — it collects nothing
        # until authorised, so this leaks a dead object rather than money.
        await session.rollback()
        logger.warning(
            "Race creating a mandate for org {}; orphaned Razorpay subscription {}",
            organization_id,
            created.get("id"),
        )
        winner = await get_mandate(session, organization_id=organization_id)
        if winner is None:
            raise MandateError("Could not create or find a mandate") from None
        return winner

    logger.info(
        "Created {} mandate {} for org {}",
        PROVIDER,
        mandate.subscription_id,
        organization_id,
    )
    return mandate


async def create_plan_mandate(
    session: AsyncSession,
    *,
    organization_id: int,
    price_paise: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> PaymentMandateModel:
    """Start the autopay authorisation for the starter plan.

    The same shape as :func:`create_rental_mandate` and for the same reasons —
    nothing is collected here, the mandate becomes usable only when the
    provider says it was authorised, and calling twice returns the live mandate
    rather than authorising a second bank instruction for one plan.

    What differs is the amount and what it buys. The rental mandate covers a
    number; this covers a number *and* the call balance that comes with it, so
    an account holding this one must not also hold a rental mandate — see
    ``NUMBER_BEARING_PURPOSES``.
    """
    existing = await get_mandate(
        session, organization_id=organization_id, purpose=PURPOSE_STARTER_PLAN
    )
    if existing is not None:
        return existing

    # Net, like every figure the ledger deals in. The bank is told the gross,
    # because that is what the customer actually pays; getting this backwards
    # is the bug the rental mandate's own comment records — a plan collected at
    # the net figure would collect no GST at all, month after month, by
    # standing instruction.
    net_paise = int(price_paise or STARTER_PLAN_PRICE_PAISE)
    profile = await get_profile(session, organization_id=organization_id)
    try:
        gross_paise = gross_up(
            taxable_paise=net_paise,
            country_code=profile.country_code,
            state_code=profile.state_code,
        )
    except TaxError as exc:
        raise MandateError(str(exc)) from exc

    plan_id = await ensure_starter_plan(price_paise=gross_paise, client=client)

    created = await _post(
        "/subscriptions",
        {
            "plan_id": plan_id,
            "total_count": RENTAL_TOTAL_COUNT,
            "customer_notify": 1,
            "notes": {
                "organization_id": str(organization_id),
                "purpose": PURPOSE_STARTER_PLAN,
            },
        },
        client=client,
    )

    mandate = PaymentMandateModel(
        organization_id=organization_id,
        provider=PROVIDER,
        purpose=PURPOSE_STARTER_PLAN,
        subscription_id=created.get("id"),
        plan_id=plan_id,
        customer_id=created.get("customer_id"),
        status=created.get("status") or MandateStatus.CREATED.value,
        short_url=created.get("short_url"),
        # The net figure, so everything reading a mandate's price reads the
        # same kind of number. The gross lives in the provider payload.
        price_paise=net_paise,
        provider_payload=created,
    )
    session.add(mandate)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        logger.warning(
            "Race creating a plan mandate for org {}; orphaned Razorpay "
            "subscription {}",
            organization_id,
            created.get("id"),
        )
        winner = await get_mandate(
            session, organization_id=organization_id, purpose=PURPOSE_STARTER_PLAN
        )
        if winner is None:
            raise MandateError("Could not create or find a plan mandate") from None
        return winner

    logger.info(
        "Created {} starter-plan mandate {} for org {}",
        PROVIDER,
        mandate.subscription_id,
        organization_id,
    )
    return mandate


async def mandate_for_numbers(
    session: AsyncSession, *, organization_id: int
) -> PaymentMandateModel | None:
    """The mandate that entitles this account to hold a number.

    The plan wins where both exist. An account on the plan has already
    authorised an amount that includes the rent, so charging its rental mandate
    as well would collect for one number twice — and the two collections would
    each look correct on their own, which is what makes it worth resolving in
    one place rather than at every call site.
    """
    for purpose in NUMBER_BEARING_PURPOSES:
        mandate = await get_mandate(
            session, organization_id=organization_id, purpose=purpose
        )
        if mandate is not None:
            return mandate
    return None


async def assert_mandate_authorised(
    session: AsyncSession, *, organization_id: int
) -> PaymentMandateModel | None:
    """Refuse to issue a number without a standing authorisation, and say why.

    Returns the mandate, or ``None`` when the requirement is switched off — the
    caller does not branch on which, so a deployment waiting on Subscriptions
    activation runs the same code path.

    Either standing instruction qualifies. The starter plan's collection
    already contains the rent, so an account on the plan is authorised for a
    number and must not be sent to take out a rental mandate as well; see
    ``mandate_for_numbers``.
    """
    mandate = await mandate_for_numbers(session, organization_id=organization_id)

    if not REQUIRE_MANDATE_FOR_NUMBERS:
        return mandate

    if mandate is None:
        raise MandateNotAuthorised(
            "A phone number needs autopay set up first, so the monthly rental "
            "can be collected. Start it from the number purchase screen, or "
            "take the starter plan, which includes a number."
        )
    if mandate.status not in MandateStatus.authorised():
        raise MandateNotAuthorised(
            "Autopay has been started but not authorised yet — this account's "
            f"mandate is {mandate.status}. Complete the authorisation and try "
            "again."
        )
    return mandate


async def apply_subscription_event(session: AsyncSession, *, event: dict) -> dict:
    """Move a mandate to the state a verified provider event puts it in.

    Deliberately tolerant of unknown events: the provider adds them, and
    treating an unrecognised one as an error would have them retried for ever.
    Deliberately intolerant of *known* events for unknown subscriptions — those
    are logged, because they mean either a second environment sharing this
    webhook or somebody probing it.
    """
    event_type = event.get("event") or ""
    if event_type not in _EVENT_STATUS:
        return {"status": "ignored", "event": event_type}

    entity = ((event.get("payload") or {}).get("subscription") or {}).get(
        "entity"
    ) or {}
    subscription_id = entity.get("id")
    if not subscription_id:
        return {"status": "ignored", "event": event_type, "reason": "no_subscription"}

    mandate = await session.scalar(
        select(PaymentMandateModel).where(
            PaymentMandateModel.provider == PROVIDER,
            PaymentMandateModel.subscription_id == subscription_id,
        )
    )
    if mandate is None:
        logger.warning(
            "Razorpay {} for unknown subscription {}", event_type, subscription_id
        )
        return {"status": "unknown_subscription", "subscription_id": subscription_id}

    next_status = _EVENT_STATUS[event_type]
    now = datetime.now(UTC)

    if next_status is None:
        mandate.provider_payload = event
        await session.flush()
        return {"status": "noted", "event": event_type, "mandate_id": mandate.id}

    # A terminal state is final. A late-delivered `activated` after a
    # `cancelled` would otherwise revive an authorisation the customer's bank
    # has already withdrawn, and the next collection would fail at the bank
    # while our records said it should have worked.
    if mandate.status in TERMINAL:
        logger.info(
            "Ignoring {} for mandate {} already in terminal state {}",
            event_type,
            mandate.id,
            mandate.status,
        )
        return {
            "status": "terminal",
            "mandate_id": mandate.id,
            "mandate_status": mandate.status,
        }

    previous = mandate.status
    mandate.status = next_status
    mandate.provider_payload = event

    if next_status in MandateStatus.authorised() and mandate.authorised_at is None:
        mandate.authorised_at = now
    if next_status == MandateStatus.CANCELLED.value:
        mandate.cancelled_at = now
    if event_type == "subscription.charged":
        mandate.last_charged_at = now
        mandate.last_failure_reason = None
    if next_status in (MandateStatus.HALTED.value, MandateStatus.PENDING.value):
        mandate.last_failure_reason = (
            entity.get("error_description")
            or f"Provider reported {event_type.split('.')[-1]}"
        )

    await session.flush()

    if previous != next_status:
        logger.info(
            "Mandate {} for org {}: {} -> {} ({})",
            mandate.id,
            mandate.organization_id,
            previous,
            next_status,
            event_type,
        )

    return {
        "status": "updated",
        "mandate_id": mandate.id,
        "mandate_status": next_status,
        "previous_status": previous,
        "event": event_type,
        # The rental code reads this to decide whether a period was collected by
        # the bank rather than the balance.
        "charged": event_type == "subscription.charged",
    }


async def cancel_mandate(
    session: AsyncSession,
    *,
    organization_id: int,
    client: httpx.AsyncClient | None = None,
) -> PaymentMandateModel | None:
    """Withdraw the standing authorisation at the provider and locally.

    The local row is marked cancelled **after** the provider accepts, never
    before: a mandate we believe is gone but which the bank still honours is a
    collection nobody is expecting.
    """
    mandate = await get_mandate(session, organization_id=organization_id)
    if mandate is None:
        return None

    if mandate.subscription_id:
        await _post(
            f"/subscriptions/{mandate.subscription_id}/cancel",
            {"cancel_at_cycle_end": 0},
            client=client,
        )

    mandate.status = MandateStatus.CANCELLED.value
    mandate.cancelled_at = datetime.now(UTC)
    await session.flush()
    logger.info(
        "Cancelled mandate {} for org {}", mandate.subscription_id, organization_id
    )
    return mandate
