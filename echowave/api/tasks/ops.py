"""Background work for stream ops (handoff 34, 35, 15 H).

``run_ops_command`` executes one accepted command; the compare-and-swap in
``commands.execute`` makes a duplicate enqueue harmless. ``sweep_ops`` runs
every five minutes: expire unapproved requests, lift timed pauses, check
spend against the cost-stop ceilings, and send the analytics outbox. Each
part is behind its own flag and none can stop the others.
"""

from loguru import logger

from api.db import db_client


async def run_ops_command(_ctx, command_id: int) -> None:
    from api.services.ops import commands

    async with db_client.async_session() as session:
        view = await commands.execute(session, command_id)
        await session.commit()
    if view is not None:
        # After the commit, so every worker re-reads the new value.
        await commands.after_commit(view)


async def sweep_ops(_ctx) -> dict:
    from api.services import features
    from api.services.ops import commands, cost_stop, telemetry

    out: dict = {}
    if features.is_on("ops_console"):
        try:
            async with db_client.async_session() as session:
                out["commands"] = await commands.expire_and_lift(session)
        except Exception as exc:  # noqa: BLE001 - one part must not stop the rest
            logger.exception("Ops command sweep failed: {}", type(exc).__name__)
    try:
        async with db_client.async_session() as session:
            out["cost_stop"] = await cost_stop.evaluate(session)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Cost stop evaluation failed: {}", type(exc).__name__)
    try:
        async with db_client.async_session() as session:
            out["analytics"] = await telemetry.dispatch_pending(session)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Analytics dispatch failed: {}", type(exc).__name__)
    return out
