"""Decibyl's own numbers, lent out so a trial account can place a real call.

Until this existed, an account with no telephony configuration could not dial at
all: `/telephony/initiate-call` resolves the org's own configuration and fails
with ``telephony_not_configured`` before it reaches anything else. That is a
reasonable rule for a customer running their own carrier account and a wall in
front of everybody evaluating the product, who has no carrier account and is
trying to find out whether the thing works.

Twilio answers this exact question the same way. Every caller-ID verification
call it places comes from one shared number, ``+14157234000``, which is
Twilio's and never becomes a customer's. This is that.

**Three rules, each of which is enforced somewhere other than good intentions.**

*Outbound only.* The numbers are excluded from both inbound route lookups and
from the routing-conflict check (`db/telephony_phone_number_client`). Inbound
dispatch keys on `(provider, account_id, called number)` with no organization in
it, so a shared number that could be dialled in would answer one customer's
caller as whichever tenant the database returned first.

*Only to a number the account has proved it holds.* Otherwise this is a free,
authenticated way to make Decibyl ring a stranger — the same objection that
`verified_numbers` exists to answer, except that here the carriage is ours.
Callers pass ``require_verified`` and the gate is applied by the route.

*Capacity is capped, and the cap is not the number count.* A caller ID is a
From header rather than a channel, so one shared number can originate as many
simultaneous calls as the carrier account allows — the pool size bounds nothing
by itself. This docstring used to claim "a third simultaneous trial call waits",
which was never true: the caller ID is chosen with ``random.choice`` and nothing
reserved it. What bounds the pool is :data:`SHARED_OUTBOUND_SCOPE`, a
platform-wide concurrency counter taken in the same atomic acquisition as the
org's own slot (see ``routes/telephony.py``). The per-organization limit cannot
do that job: it counts each account separately, so ten evaluators dialling at
once sit comfortably inside ten separate limits while one carrier account — ours
— carries every minute.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.db import db_client
from api.services.telephony import registry
from api.services.telephony.base import TelephonyProvider

#: Concurrency scope for trial calls placed on the shared caller IDs.
#:
#: Deliberately not keyed by organization: the counter it names is a platform
#: total, because the thing being bounded — our carrier account, our credentials
#: and minutes billed to nobody — is shared by every account using the pool. See
#: ``rate_limiter.try_acquire_concurrent_slot_details``, which keys the scope
#: counter on this string alone.
SHARED_OUTBOUND_SCOPE = "shared_outbound"


class NoSharedNumber(Exception):
    """No shared caller ID is available for this deployment.

    A refusal the caller surfaces, not an error to retry: it means staff have
    not put any numbers in the pool, which no amount of retrying changes.
    """


async def pool_addresses() -> list[str]:
    """Every shared caller ID currently in service, as E.164 strings."""
    rows = await db_client.list_shared_outbound_numbers()
    return [row.address_normalized for row in rows]


async def is_configured() -> bool:
    """Whether this deployment can lend an account a number at all.

    Read rather than assumed, so a screen can offer the trial call only when it
    would actually work — the alternative is a button whose only answer is an
    error nobody can act on.
    """
    return bool(await pool_addresses())


async def get_provider() -> tuple[int, TelephonyProvider, list[str]]:
    """The provider to dial a trial call with, and the numbers to dial from.

    Returns ``(telephony_configuration_id, provider, from_numbers)``. The
    configuration is Decibyl's own — the one the shared numbers hang off — so
    the credentials, the carrier account and the rent are all ours, and none of
    it is attributed to the account placing the call.
    """
    rows = await db_client.list_shared_outbound_numbers()
    if not rows:
        raise NoSharedNumber(
            "No shared caller ID is configured on this deployment, so a trial "
            "call cannot be placed. Add one under staff telephony."
        )

    # Every shared number is expected to sit on one platform configuration. If
    # staff have spread them across several, the first is used and the rest are
    # reported — silently dialling from one and ignoring the others would make
    # the pool quietly smaller than the screen says it is.
    config_ids = {row.telephony_configuration_id for row in rows}
    if len(config_ids) > 1:
        logger.warning(
            "Shared outbound numbers span {} telephony configurations {}; "
            "using the first. Keep them on one configuration so the pool is "
            "the size it appears to be.",
            len(config_ids),
            sorted(config_ids),
        )

    configuration_id = rows[0].telephony_configuration_id
    row = await db_client.get_telephony_configuration(configuration_id)
    if row is None:
        raise NoSharedNumber(
            "The shared caller ID points at a telephony configuration that no "
            "longer exists."
        )

    from api.services.telephony.factory import _normalize_with_phone_numbers

    config: dict[str, Any] = await _normalize_with_phone_numbers(row)
    addresses = [
        r.address_normalized
        for r in rows
        if r.telephony_configuration_id == configuration_id
    ]
    # The pool this call may dial from is the shared numbers, not every number
    # on the configuration — staff may keep other numbers there.
    config["from_numbers"] = addresses

    spec = registry.get(row.provider)
    return configuration_id, spec.provider_cls(config), addresses
