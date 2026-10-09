"""What the staff page reads: open alerts, recent resolutions, the summaries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from api.db import db_client
from api.services.ops_alerts import incidents, metering, signals, summary
from api.services.ops_alerts import thresholds as t


async def alerts(*, now: datetime | None = None, client=None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    ticks = await signals.last_ticks(client)
    return {
        "observed_at": now.isoformat(),
        "open": [
            incidents.as_public(i) for i in await incidents.open_incidents(client)
        ],
        "resolved": [
            incidents.as_public(i) for i in await incidents.history(20, client)
        ],
        "ticks": [
            {
                "name": name,
                "label": label,
                "last_completed": ticks.get(name).isoformat()
                if ticks.get(name)
                else None,
            }
            for name, label in signals.TICKS.items()
        ],
        "thresholds": {name: getattr(t, name) for name in dir(t) if name.isupper()},
    }


async def daily_summaries(
    *, now: datetime | None = None, client=None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    async with db_client.async_session() as session:
        rows = await summary.recent(session, today=metering.ist_day(now), client=client)
    return {"summaries": rows}
