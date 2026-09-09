"""Scheduled check that our own provider keys still work.

See ``services/configuration/credential_validation.py`` for why. In short: a
revoked platform key stayed ``is_active``, the managed tier it backed stayed on
offer, and the first people to find out were inbound callers listening to
silence.

Runs at startup and hourly. Hourly rather than per-call because a probe on the
dial path would add vendor latency to every call to catch something that
changes maybe twice a year, and because vendors rate-limit key checks like any
other request.
"""

from loguru import logger

from api.db import db_client
from api.enums import PostHogEvent
from api.services.configuration.credential_validation import (
    validate_stored_credentials,
)
from api.services.posthog_client import (
    POSTHOG_SYSTEM_DISTINCT_ID,
    capture_event,
    flush_posthog,
)


async def check_platform_credentials(_ctx) -> None:
    """Probe every active platform key and record the verdict."""
    try:
        async with db_client.async_session() as session:
            checks = await validate_stored_credentials(session)
    except Exception as error:  # noqa: BLE001 - a cron must not die on one bad tick
        logger.error("Platform credential check failed: {}", error)
        return

    _report(checks)

    bad = [f"{c.component}/{c.provider}" for c in checks if c.ok is False]
    if bad:
        logger.error(
            "{} of {} platform key(s) are being rejected by their provider: {}. "
            "Managed tiers on them are now withdrawn from the model picker.",
            len(bad),
            len(checks),
            ", ".join(bad),
        )
    else:
        logger.info("Platform credential check: {} key(s), none rejected", len(checks))


def _report(checks) -> None:
    """Send the transitions to PostHog.

    Transitions only. This job runs hourly, so an event per sweep would turn a
    one-day outage into twenty-four identical rows and bury the one that says
    when it started — which is the only one worth alerting on. A key that is
    still rejected on the next sweep has not changed and says nothing new.

    The subject is a key we hold, not a customer, so these carry no
    organization and no user; see POSTHOG_SYSTEM_DISTINCT_ID. The provider and
    component are on the event because "which vendor" is the first question
    anyone reading the alert will ask, and the key itself never is — not even
    its last four, which would put a credential fragment in an analytics
    product that is not the place for one.
    """
    changed = [c for c in checks if c.changed]
    if not changed:
        return

    for check in changed:
        capture_event(
            distinct_id=POSTHOG_SYSTEM_DISTINCT_ID,
            event=(
                PostHogEvent.PLATFORM_KEY_REJECTED
                if check.ok is False
                else PostHogEvent.PLATFORM_KEY_RECOVERED
            ),
            properties={"component": check.component, "provider": check.provider},
        )

    # This is a worker job, not a request: without a flush the events sit in the
    # client's queue until something else happens to send them, which for an
    # alert about a broken key is exactly the wrong latency.
    flush_posthog()
