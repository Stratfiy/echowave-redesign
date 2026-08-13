"""Do-not-disturb scrubbing and the TCCCPR calling window.

Placing a call to a number on a do-not-disturb registry, or outside the hours
the regulator permits, is a regulatory event rather than a delivery failure.
That shapes every decision in this module:

* **It fails closed.** A lookup that errors refuses the call. This is the
  opposite of the balance reservation path, which deliberately fails *open*
  because the cost of refusing a funded customer's call exceeds the cost of an
  uncosted one. Here the asymmetry runs the other way — the cost of one call
  too many is a complaint to TRAI, and the cost of one call too few is a retry.

* **It is checked immediately before dialling, never at queue time.** A
  campaign started at 20:55 dials into the night. A check that ran once at
  start would pass and then be wrong for every row after 21:00.

* **A refusal is recorded with its reason**, so "why did this row never get
  called" has an answer that is not "look at the logs".

On the calling window: TCCCPR sets 09:00–21:00 in the *recipient's* local time.
We hold the organization's configured timezone as the proxy for that. For a
deployment dialling Indian numbers from an Indian account the two are the same
zone, so the proxy is exact. It stops being exact the moment someone dials
across timezones, and the honest fix then is per-number zone resolution from
the numbering plan rather than a wider window — noted here so the next person
does not discover the assumption by being wrong about it.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

from api.constants import (
    CALLING_HOURS_END,
    CALLING_HOURS_START,
    DEFAULT_ORGANIZATION_TIMEZONE,
    DND_ENFORCEMENT_ENABLED,
)


class CallRefused(Exception):
    """Base for a refusal that is the caller's obligation, not an error."""

    #: Short, stable token stored against the queued row and returned to the
    #: API caller. The human sentence changes; this does not.
    reason: str = "refused"


class DoNotDisturbListed(CallRefused):
    reason = "dnd_listed"


class OutsideCallingHours(CallRefused):
    reason = "outside_calling_hours"


# ---------------------------------------------------------------------------
# Number normalisation
#
# The registry is only useful if the number a customer uploads and the number
# the dialler uses reduce to the same key. "+91 98765 43210", "098765 43210"
# and "9876543210" are one number written three ways, and a list that treats
# them as three is a list that does not block anything.
# ---------------------------------------------------------------------------

_NON_DIGITS = re.compile(r"\D")

#: India. A deployment elsewhere overrides the country code and the national
#: number length together — they are not independently meaningful.
_DEFAULT_COUNTRY_CODE = "91"
_NATIONAL_LENGTH = 10


def normalise_number(raw: str | None) -> str | None:
    """Reduce a written phone number to a comparable key, or None.

    Returns digits only, country code included, no leading ``+``. None means
    "this is not a phone number" — the caller decides whether that is a
    validation error or a row to skip, because the answer differs between an
    API request and row 4,000 of an uploaded CSV.
    """
    if not raw:
        return None

    digits = _NON_DIGITS.sub("", str(raw))
    if not digits:
        return None

    # A single leading zero is the Indian trunk prefix, not part of the number.
    if len(digits) == _NATIONAL_LENGTH + 1 and digits.startswith("0"):
        digits = digits[1:]

    # 00 is the international access code in most of the world; treat it the
    # way a '+' would be treated.
    if digits.startswith("00"):
        digits = digits[2:]

    if len(digits) == _NATIONAL_LENGTH:
        digits = _DEFAULT_COUNTRY_CODE + digits

    # Anything shorter than a national number cannot be dialled and must not
    # be stored as though it could: a 5-digit "number" in a DND list would
    # match nothing, and silently keeping it makes the list look longer than
    # the protection it actually provides.
    if len(digits) < _NATIONAL_LENGTH:
        return None

    return digits


# ---------------------------------------------------------------------------
# The calling window
# ---------------------------------------------------------------------------


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hour, _, minute = value.partition(":")
        return time(int(hour), int(minute or 0))
    except (ValueError, TypeError):
        logger.warning(
            f"Invalid calling-hours value {value!r}; using {fallback.isoformat()}"
        )
        return fallback


def resolve_zone(timezone_name: str | None) -> ZoneInfo:
    """The zone to judge the calling window in.

    An unknown zone name falls back rather than raising. The alternative is an
    organization typing a bad timezone into settings and thereby switching off
    every outbound call it makes, which is a worse failure than judging the
    window in the default zone and saying so in the log.
    """
    for candidate in (timezone_name, DEFAULT_ORGANIZATION_TIMEZONE):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(f"Unknown timezone {candidate!r} for calling-hours check")
    return ZoneInfo("UTC")


def within_calling_hours(
    *,
    timezone_name: str | None,
    now: datetime | None = None,
    start: str | None = None,
    end: str | None = None,
) -> bool:
    """Is *now*, seen from the organization's zone, inside the permitted window?

    Windows that wrap midnight are supported so the bounds stay configurable,
    even though the regulated Indian window does not wrap.
    """
    zone = resolve_zone(timezone_name)
    moment = (now or datetime.now(zone)).astimezone(zone)

    window_start = _parse_hhmm(start or CALLING_HOURS_START, time(9, 0))
    window_end = _parse_hhmm(end or CALLING_HOURS_END, time(21, 0))
    current = moment.time()

    if window_start <= window_end:
        return window_start <= current < window_end
    return current >= window_start or current < window_end


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


async def assert_may_call(
    organization_id: int,
    phone_number: str,
    *,
    timezone_name: str | None = None,
    now: datetime | None = None,
    db=None,
) -> str:
    """Refuse the call if the number is listed, or the window is closed.

    Returns the normalised number on success, so callers that need the key for
    logging or storage do not normalise it a second time and risk disagreeing
    with the check that just passed.

    Raises DoNotDisturbListed or OutsideCallingHours. Both are refusals the
    caller is expected to surface, not errors to retry.
    """
    if not DND_ENFORCEMENT_ENABLED:
        return normalise_number(phone_number) or phone_number

    normalised = normalise_number(phone_number)
    if normalised is None:
        # Not dialable. Refusing here rather than downstream keeps a malformed
        # row from consuming a concurrency slot to fail at the trunk.
        raise DoNotDisturbListed(
            f"{phone_number!r} is not a dialable phone number."
        )

    if not within_calling_hours(timezone_name=timezone_name, now=now):
        raise OutsideCallingHours(
            f"Calls are only placed between {CALLING_HOURS_START} and "
            f"{CALLING_HOURS_END} in your organization's timezone. "
            "This call was not placed."
        )

    from api.db import db_client as default_db_client

    client = db or default_db_client
    try:
        listed = await client.is_number_dnd_listed(organization_id, normalised)
    except Exception as exc:  # noqa: BLE001 — see the module docstring
        # Fail closed. An unavailable list is not evidence that a number is
        # absent from it, and this is the one gate where guessing wrong is a
        # regulatory event rather than a lost call.
        logger.error(
            f"DND lookup failed for organization {organization_id}: {exc}. "
            "Refusing the call."
        )
        raise DoNotDisturbListed(
            "The do-not-disturb list could not be checked, so this call was "
            "not placed. Try again shortly."
        ) from exc

    if listed:
        raise DoNotDisturbListed(
            "This number is on your do-not-disturb list and was not called."
        )

    return normalised
