"""Who gets through to an inbound number, and what the agent knows about them.

A published number is dialled by anyone who has it, which makes it the one
surface where the caller is not somebody the account chose. Two questions have
to be answered while the phone is ringing:

**Do we know this caller?** If the number names a contact list, the caller is
looked up in it and their stored attributes are preloaded into the run, so the
agent opens knowing who it is talking to rather than asking. A number can also
refuse a caller it does not recognise — for a line given only to existing
customers — but that is off unless somebody turns it on, because a published
number that quietly stops answering strangers is a support ticket nobody
connects to a settings change.

**Have they called too often?** A per-caller limit over a rolling window, with
an allow list that bypasses it. The window matters more than it looks: a
lifetime cap locks out a legitimate repeat caller permanently, and the only
person positioned to notice is the caller, who by definition cannot reach us.

**It fails open.** Redis unreachable, a lookup that errors — the call goes
through. This is the opposite of ``compliance/dnd``, deliberately, and the
asymmetry is the reason: dialling somebody on a DND registry is a regulatory
event, while wrongly refusing an inbound call is a customer who could not reach
their supplier and does not know why. Neither the spam limit nor the
known-caller check is a legal control, so when we cannot evaluate one, the
customer's call is worth more than the rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL
from api.utils.telephony_address import normalize_telephony_address

#: Namespaced so an operator reading Redis can tell what a key is for.
_PREFIX = "telephony:inbound:calls:"

#: Ceiling on the rolling window, so a mis-typed value cannot pin a counter in
#: Redis for a year. Four weeks is longer than any support policy needs and
#: short enough that a mistake ages out on its own.
MAX_WINDOW_HOURS = 24 * 28


@dataclass
class InboundDecision:
    """Whether to answer, and what the agent should already know.

    ``allowed`` is the only field a caller must handle. ``reason`` names which
    rule refused, for the log and the run record — "the call was rejected" with
    no reason is the thing that makes this feature unsupportable.
    """

    allowed: bool
    reason: str | None = None
    #: Merged into the run's ``initial_context``. Empty for an unknown caller.
    contact_context: dict[str, Any] = field(default_factory=dict)


def _normalized(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return normalize_telephony_address(raw).canonical
    except ValueError:
        return None


def is_allow_listed(caller: str | None, allow_list: list | None) -> bool:
    """Whether this caller bypasses the limit.

    Both sides are normalized at comparison time rather than trusting what was
    stored: an allow list typed into a form contains spaces, brackets and a
    leading zero, and the carrier sends none of those. Comparing the raw
    strings would produce an allow list that silently never matches — which
    reads exactly like the feature working, right up until the office line is
    refused.
    """
    if not allow_list:
        return False
    target = _normalized(caller)
    if not target:
        return False
    return any(_normalized(entry) == target for entry in allow_list)


async def _count_and_increment(key: str, window_hours: int) -> int:
    """Calls placed in this window, counting the one now arriving.

    A fixed window rather than a sliding one: the counter is a single key with
    a TTL, so it costs one round trip and needs no cleanup. The cost is that a
    caller can place up to twice the limit across a window boundary, which for
    a spam control is an acceptable trade against the bookkeeping a sliding
    window needs on every inbound call.
    """
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_hours * 3600, nx=True)
            results = await pipe.execute()
        return int(results[0])
    finally:
        await client.aclose()


async def evaluate(
    *,
    caller: str | None,
    phone_number,
    lookup_contact=None,
) -> InboundDecision:
    """Decide whether this inbound call is answered, and preload what we know.

    ``phone_number`` is a ``TelephonyPhoneNumberModel``. ``lookup_contact`` is
    an awaitable taking ``(contact_list_id, normalized_caller)`` and returning a
    contact row or ``None``; injected so this stays a decision rather than a
    database module, and so the caller owns org scoping.
    """
    normalized_caller = _normalized(caller)

    contact = None
    list_id = getattr(phone_number, "inbound_contact_list_id", None)
    if list_id and normalized_caller and lookup_contact is not None:
        try:
            contact = await lookup_contact(list_id, normalized_caller)
        except Exception as exc:  # noqa: BLE001 - fail open, see module docstring
            logger.warning(
                "Contact lookup failed for inbound call from {}: {}. Treating the "
                "caller as unknown.",
                normalized_caller,
                exc,
            )

    if getattr(phone_number, "inbound_require_known_caller", False) and contact is None:
        # Only refuse when we actually asked the question. With no list
        # configured there is nothing to be unknown to, and refusing every
        # caller because somebody left the switch on would take a number off
        # the air completely.
        if list_id:
            return InboundDecision(allowed=False, reason="caller_not_in_contact_list")
        logger.warning(
            "Number {} requires a known caller but names no contact list; "
            "accepting the call.",
            getattr(phone_number, "address_normalized", "?"),
        )

    contact_context: dict[str, Any] = {}
    if contact is not None:
        attributes = getattr(contact, "attributes", None) or {}
        if isinstance(attributes, dict):
            contact_context.update(attributes)
        # Set after the attributes so a column named "contact_name" in
        # somebody's CSV cannot overwrite the identity we resolved.
        contact_context["contact_id"] = getattr(contact, "id", None)
        contact_context["contact_name"] = getattr(contact, "name", None)
        contact_context["contact_is_known"] = True

    limit = getattr(phone_number, "inbound_max_calls_per_caller", None)
    # <= 0 is how a form says "unlimited" — Bolna spells that -1, and a UI that
    # cannot express it any other way should not be able to set a limit of zero
    # and take the number off the air.
    if not limit or limit <= 0 or not normalized_caller:
        return InboundDecision(allowed=True, contact_context=contact_context)

    if is_allow_listed(
        normalized_caller, getattr(phone_number, "inbound_allow_list", None)
    ):
        return InboundDecision(allowed=True, contact_context=contact_context)

    window_hours = getattr(phone_number, "inbound_call_window_hours", None) or 24
    window_hours = max(1, min(int(window_hours), MAX_WINDOW_HOURS))

    key = f"{_PREFIX}{getattr(phone_number, 'id', 0)}:{normalized_caller}"
    try:
        placed = await _count_and_increment(key, window_hours)
    except Exception as exc:  # noqa: BLE001 - fail open, see module docstring
        logger.warning(
            "Could not count inbound calls from {}: {}. Allowing the call.",
            normalized_caller,
            exc,
        )
        return InboundDecision(allowed=True, contact_context=contact_context)

    if placed > limit:
        logger.info(
            "Refusing inbound call from {} to number {}: {} calls in the last {}h "
            "exceeds the limit of {}.",
            normalized_caller,
            getattr(phone_number, "address_normalized", "?"),
            placed,
            window_hours,
            limit,
        )
        # Carries the context it resolved. Computing who this is and then
        # discarding it on one of the two branches would make
        # ``contact_context`` mean "who is calling, unless we said no" — a
        # field any reader has to check ``allowed`` before trusting.
        return InboundDecision(
            allowed=False,
            reason="caller_call_limit_exceeded",
            contact_context=contact_context,
        )

    return InboundDecision(allowed=True, contact_context=contact_context)
