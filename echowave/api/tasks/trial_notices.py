"""Daily, 10:15 IST: trial ending in 3 days, tomorrow, and ended (PLAN-1)."""

from __future__ import annotations

from api.services.billing import trial


async def send_trial_notices(_ctx=None) -> dict[str, int]:
    return await trial.send_notices()
