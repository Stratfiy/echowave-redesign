"""Missed call to callback — the caller rings, hangs up, and we ring back.

The most reliable way to reach an Indian customer is to let them reach you
first. A missed call costs the caller nothing: no data, no app, no form, no
literacy in the language the form is written in. Someone who will never fill a
web form will give a missed call to a number on a hoarding, and everyone from
banks checking balances to political campaigns has run on that behaviour for
twenty years.

So a number can be put in *callback mode*: we do not answer it at all. The
carrier's inbound leg is rejected before any media is set up — the caller pays
nothing and neither do we — and an outbound call goes back to them on a chosen
agent. From the caller's side it is one interaction. From ours it is an inbound
webhook and an outbound dial, and the outbound leg is the one that carries the
conversation.

**Why the callback is not treated as cold outbound.** TCCCPR governs calls a
subscriber did not ask for. This is the opposite: the person dialled our number
seconds ago, which is about as explicit as consent gets, and refusing to call
back a DND-registered subscriber who just rang us would break the feature for
the majority of Indian handsets while protecting nobody. The organization's own
suppression list is still honoured — a number on it is there because this
account said never call this — and so is the calling window, because "they rang
at 2am" is not a reason for us to ring back at 2am.

The guards below exist because this is the one path where an inbound event
causes an outbound call. That is a loop waiting to be built, and the person who
pays for the loop is the customer.
"""

from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL

#: How long one caller is ignored after we have called them back.
#:
#: A person who rings, gets a callback, and rings again while it is still
#: coming through is not asking for a second call — they are impatient, or the
#: first callback rang on a phone that was still busy with the leg they just
#: hung up. Without this, every retry is another billed outbound call and the
#: customer's bill grows with the caller's frustration.
CALLBACK_COOLDOWN_SECONDS = 300

#: Callbacks per caller per rolling day, whatever the cooldown allows.
#:
#: The cooldown handles impatience. This handles the other case: a number that
#: rings ours all day, either because someone is bored, because an autodialler
#: on the other side has our number in a list, or because two systems have
#: found each other. Twelve is generous for a person and ruinous for a loop.
CALLBACK_DAILY_CAP = 12

#: Rolling day, in seconds. Deliberately a sliding TTL rather than a calendar
#: day: a cap that resets at midnight lets a loop run twice as hard at 23:59.
CALLBACK_DAY_SECONDS = 86400


class CallbackRefused(Exception):
    """We are not calling this person back, and the reason is not an error.

    Every subclass is an outcome the inbound webhook is expected to swallow
    quietly. The caller has already hung up — there is nobody on the line to
    tell, and raising this to the carrier as a failure would only make the
    provider retry a webhook whose whole job was to decline.
    """


class CallbackTooSoon(CallbackRefused):
    """Cooldown is still running from the previous callback."""


class CallbackCapReached(CallbackRefused):
    """This caller has had its day's worth of callbacks."""


class CallbackLoop(CallbackRefused):
    """The caller is one of our own numbers.

    The failure this prevents: a Decibyl number in callback mode receives a
    call from another Decibyl number, calls it back, and that number's own
    inbound rule answers and calls back in turn. Two agents then talk to each
    other, on the customer's money, until someone notices. It is not a
    hypothetical — pointing a test agent at a callback number is the obvious
    way to try the feature out.
    """


@dataclass(frozen=True)
class CallbackDecision:
    """Whether to call back, and what to say about it in the log."""

    caller: str
    workflow_id: int
    organization_id: int
    telephony_configuration_id: int


def is_callback_number(phone_row) -> bool:
    """Whether this number answers by calling back rather than by answering.

    Callback mode is a property of the *number*, not of the workflow: the same
    agent can serve an inbound line in one place and a missed-call hoarding in
    another, and it is the number printed on the hoarding that decides which.
    """
    return bool(getattr(phone_row, "callback_workflow_id", None))


class _Guard:
    """Redis-backed counters for the two rate limits.

    Its own class rather than module functions so tests can substitute a fake
    without patching a module-level connection into other tests' paths.
    """

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _client(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    @staticmethod
    def _cooldown_key(organization_id: int, caller: str) -> str:
        return f"missed_call:cooldown:{organization_id}:{caller}"

    @staticmethod
    def _daily_key(organization_id: int, caller: str) -> str:
        return f"missed_call:daily:{organization_id}:{caller}"

    async def reserve(self, organization_id: int, caller: str) -> None:
        """Claim this caller's next callback, or raise saying why not.

        Deliberately a *reservation* and not a check: the cooldown key is set
        in the same round trip that tests it. A person who taps redial three
        times in two seconds produces three concurrent webhooks, and a
        check-then-set would let all three through the gap between the two
        operations and place three calls.
        """
        client = await self._client()

        # SET NX is the reservation. It succeeds for exactly one of N racing
        # webhooks; the losers get None and are refused, which is the correct
        # outcome for all but the first.
        won = await client.set(
            self._cooldown_key(organization_id, caller),
            "1",
            ex=CALLBACK_COOLDOWN_SECONDS,
            nx=True,
        )
        if not won:
            raise CallbackTooSoon(
                f"{caller} was called back less than "
                f"{CALLBACK_COOLDOWN_SECONDS}s ago."
            )

        daily_key = self._daily_key(organization_id, caller)
        used = await client.incr(daily_key)
        if used == 1:
            # First of the day — start the window. Set only on the first
            # increment so the window slides from the first call rather than
            # being pushed forward by every subsequent one, which would let a
            # persistent caller hold their allowance open indefinitely.
            await client.expire(daily_key, CALLBACK_DAY_SECONDS)
        if used > CALLBACK_DAILY_CAP:
            raise CallbackCapReached(
                f"{caller} has had {CALLBACK_DAILY_CAP} callbacks in the last "
                "24 hours."
            )


_guard = _Guard()


async def authorise(
    *,
    organization_id: int,
    caller: str,
    our_numbers: set[str],
    guard: _Guard | None = None,
) -> None:
    """Decide whether this missed call earns a callback.

    ``our_numbers`` is every number the platform itself dials from or answers
    on, normalised. It is passed in rather than looked up here so the caller
    can reuse a set it already has, and so this stays a pure decision that a
    test can drive without a database.

    Raises a CallbackRefused subclass. Returning normally means place the call.
    """
    if caller in our_numbers:
        raise CallbackLoop(
            f"{caller} is one of our own numbers; refusing to call it back."
        )

    await (guard or _guard).reserve(organization_id, caller)


def loop_guard_numbers(rows) -> set[str]:
    """The normalised set of numbers we must never call back.

    Built from `address_normalized` because that is the column the inbound
    lookup matches on, so a number stored one way and compared another is a
    hole in exactly the guard that is hardest to notice is missing.
    """
    numbers = set()
    for row in rows:
        normalised = getattr(row, "address_normalized", None)
        if normalised:
            numbers.add(normalised)
    return numbers


def log_refusal(caller: str, exc: CallbackRefused) -> None:
    """One place that decides how loud a refusal is.

    A cooldown or a cap is ordinary traffic shaping and belongs at info. A loop
    is a misconfiguration that will keep costing money until somebody changes
    something, so it gets a warning with the number in it.
    """
    if isinstance(exc, CallbackLoop):
        logger.warning("Missed-call callback refused for {}: {}", caller, exc)
    else:
        logger.info("Missed-call callback refused for {}: {}", caller, exc)

# ---------------------------------------------------------------------------
# Placing the call
# ---------------------------------------------------------------------------


async def place_callback(event) -> int:
    """Dial the caller back on the number's callback agent. Returns the run id.

    Raises CallbackRefused when a guard declines, and lets anything else
    propagate to the task, which records it as a failure on the event row.

    The guard order matters and is not arbitrary: the cheap local checks run
    before anything that costs a database round trip or a concurrency slot, so
    a caller in a loop cannot make us do work by ringing repeatedly.
    """
    from api.db import db_client
    from api.services.compliance import dnd
    from api.services.organization_preferences import get_organization_preferences
    from api.services.telephony.factory import get_telephony_provider_by_id

    number_row = await db_client.get_phone_number_for_org(
        event.telephony_phone_number_id, event.organization_id
    )
    if number_row is None or not number_row.callback_workflow_id:
        raise CallbackRefused(
            "The number that received this call is no longer in callback mode."
        )

    our_numbers = set(
        await db_client.list_normalized_addresses_for_organization(
            event.organization_id
        )
    )
    await authorise(
        organization_id=event.organization_id,
        caller=event.caller,
        our_numbers=our_numbers,
    )

    preferences = await get_organization_preferences(
        event.organization_id, db=db_client
    )
    try:
        # The calling window applies. "They rang at 2am" is not a reason for us
        # to ring back at 2am — TCCCPR protects the subscriber's night, and
        # they hung up before we could ask whether they meant to be called.
        #
        # The DND *list* is a different question and it is enforced here on
        # purpose, unlike the window's usual exemptions: an organization's
        # suppression list says never call this number, and someone dialling in
        # does not overrule the account's own instruction not to dial out.
        dialable = await dnd.assert_may_call(
            event.organization_id,
            event.caller,
            timezone_name=preferences.timezone,
            db=db_client,
        )
    except dnd.CallRefused as exc:
        raise CallbackRefused(str(exc)) from exc

    workflow = await db_client.get_workflow(
        number_row.callback_workflow_id, organization_id=event.organization_id
    )
    if workflow is None:
        raise CallbackRefused("The callback agent for this number no longer exists.")

    provider = await get_telephony_provider_by_id(
        number_row.telephony_configuration_id, event.organization_id
    )

    from api.services.telephony.outbound import dial_workflow

    return await dial_workflow(
        workflow=workflow,
        organization_id=event.organization_id,
        to_number=dialable,
        provider=provider,
        telephony_configuration_id=number_row.telephony_configuration_id,
        source="missed_call",
        extra_context={
            "trigger_source": "missed_call",
            "missed_call_event_id": event.id,
            "missed_call_number_id": event.telephony_phone_number_id,
        },
    )
