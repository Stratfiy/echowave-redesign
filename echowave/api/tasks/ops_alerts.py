"""Operational alerts every five minutes, and the cost summary at 08:45 IST.

Both are no-ops while ``ops_alerts`` is off. See ``services/ops_alerts``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.db import db_client
from api.services import features


async def run_ops_alerts(_ctx=None, *, now: datetime | None = None) -> dict:
    from api.services.ops_alerts import runner

    return await runner.evaluate(now=now)


async def send_daily_cost_summary(_ctx=None, *, now: datetime | None = None) -> dict:
    """Yesterday's summary (IST), mailed once. Safe to re-run."""
    if not features.is_on("ops_alerts"):
        return {"skipped": "flag_off"}
    from api.services.ops_alerts import metering, summary

    now = now or datetime.now(UTC)
    yesterday = metering.ist_day(now) - timedelta(days=1)
    async with db_client.async_session() as session:
        return await summary.send_for(session, yesterday)
