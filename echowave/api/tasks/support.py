"""Background work for launch stream `support` (services/support/actions.py).

``run_support_action`` executes one approved, queued action exactly once
(the claim is a compare-and-swap from queued to running). The sweep marks
runs whose worker died as outcome unknown and lapses old requests. Both do
nothing while ``support_actions`` is off.
"""

from __future__ import annotations

from loguru import logger


async def run_support_action(_ctx, action_id: int) -> str | None:
    from api.services import support
    from api.services.support import actions

    if not support.actions_enabled():
        logger.warning("Support action {} not run: support_actions is off", action_id)
        return None
    return await actions.execute(int(action_id))


async def sweep_support_actions(_ctx) -> None:
    from api.services import support
    from api.services.support import actions

    if not support.actions_enabled():
        return
    result = await actions.sweep()
    if result["outcome_unknown"] or result["expired"]:
        logger.warning("Support actions swept: {}", result)
