"""Retire plan balance whose cycle ended and was never renewed.

``grant_plan_cycle`` expires the previous cycle at the moment the next one is
collected, which covers every account that keeps paying. This job covers the
ones that stop: an account that cancels, or whose bank stops honouring the
mandate, gets no next collection — and without this its last cycle's balance
would sit there for ever, which is exactly the rollover-indefinitely behaviour
the expiry decision replaced.

Daily rather than monthly, and with grace rather than to the day. A collection
can land late — a bank retry, a weekend — and expiring balance the customer is
about to renew would be the worst possible timing for the most alarming line a
billing screen can show. ``CYCLE_GRACE_DAYS`` is longer than a month for that
reason: erring long costs a few days of balance, erring short takes money from
somebody who paid for it.

Safe to re-run. Every expiry is keyed on the grant it retires, so this job and
a late collection can reach the same grant and only one debit lands.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.services.billing import plans


async def expire_lapsed_plan_balance(ctx: dict | None = None) -> dict[str, int]:
    """Sweep every account whose last plan cycle has run out. Never raises."""
    try:
        async with db_client.async_session() as session:
            counters = await plans.sweep_lapsed_plan_balance(session)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — a cron must not die on one bad row
        logger.error("Plan balance expiry sweep failed: {}", exc)
        return {"considered": 0, "expired": 0, "paise": 0}

    if counters["expired"]:
        logger.info(
            "Expired unused plan balance on {} of {} lapsed cycles (₹{:.2f})",
            counters["expired"],
            counters["considered"],
            counters["paise"] / 100,
        )
    return counters
