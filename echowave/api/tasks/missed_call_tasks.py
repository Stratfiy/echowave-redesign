"""Place the callback for a missed call, out of band.

Deliberately not done inside the inbound webhook. The carrier is holding that
request open waiting for instructions, and every provider treats a slow answer
as a failed one — so a webhook that dialled before replying would trade the
caller's ring for a carrier timeout, and the caller would hear nothing while we
did the useful work. The webhook's only job is to decline fast; this is where
the call actually gets made.

Failure here is quiet by design. There is nobody on the line to tell: the
caller hung up seconds ago, which is the entire premise. The failure is
recorded on the event row instead, which is what the operator reads.
"""

from loguru import logger

from api.db import db_client
from api.services.telephony import missed_call


async def place_missed_call_callback(ctx, event_id: int, organization_id: int) -> None:
    """Ring back whoever rang us, and record what happened either way.

    Reads the event rather than taking the caller as an argument: the row is
    already the record of this ring, and passing the number separately would
    let the job dial a number the row does not mention.
    """
    event = await db_client.get_missed_call(event_id, organization_id=organization_id)
    if event is None:
        # Retention purge, or a deleted organization. Nothing to call back and
        # nothing to record it on.
        logger.warning(f"Missed-call event {event_id} vanished before callback")
        return

    if event.outcome != "pending":
        # ARQ re-delivers a job whose worker died mid-run. Without this, a
        # worker restart at the wrong moment rings the caller a second time —
        # and the cooldown will not save us, because the first attempt already
        # consumed it.
        logger.info(
            f"Missed-call event {event_id} is already {event.outcome}; not "
            "calling back again"
        )
        return

    try:
        workflow_run_id = await missed_call.place_callback(event)
    except missed_call.CallbackRefused as exc:
        missed_call.log_refusal(event.caller, exc)
        await db_client.resolve_missed_call(
            event_id,
            organization_id=organization_id,
            outcome="refused",
            refusal_reason=str(exc),
        )
        return
    except Exception as exc:  # noqa: BLE001 -- an outcome to record, see docstring
        logger.error(f"Missed-call callback to {event.caller} failed: {exc}")
        await db_client.resolve_missed_call(
            event_id,
            organization_id=organization_id,
            outcome="failed",
            refusal_reason=str(exc),
        )
        return

    await db_client.resolve_missed_call(
        event_id,
        organization_id=organization_id,
        outcome="called_back",
        workflow_run_id=workflow_run_id,
    )
