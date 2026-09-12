"""The clock for every routine, once a minute.

Cross-tenant on purpose: this is the only job in the product that legitimately
has no organisation, because it is not acting for anybody -- it is the clock.
Each routine's own tenant is carried forward into the run it starts.

Two rules keep this tick honest, and both are about what gets written.

**An ordinary skip writes nothing.** A routine not yet due, already run today,
or resting on a Sunday is the system working. Recording those would be 1,440
rows a day per routine and a timeline nobody could read, which is a worse
outcome than the silence it was meant to fix.

**A skip worth knowing about writes once per episode, not once a minute.** A
broken connector or a missed run is recorded the first tick it appears and
then deduplicated against what the routine already says, so "your Shopify is
down and the run has stopped" arrives as one line rather than as a thousand.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.compliance import dnd
from api.services.organization_preferences import get_organization_preferences
from api.services.workflow import agent_timeline, routines
from api.tasks.function_names import FunctionNames


async def fire_due_routines(ctx) -> None:
    """Look at every armed routine and start the ones that are due."""
    armed = await db_client.armed_routines()
    if not armed:
        return

    now = datetime.now(UTC)
    fired = 0

    # Preferences and failing apps are per organisation, not per routine. Two
    # routines in one clinic would otherwise each pay for the same two queries
    # every minute of every day.
    preferences: dict[int, object] = {}
    broken: dict[int, set[str]] = {}

    for routine in armed:
        try:
            organization_id = routine.organization_id
            if organization_id not in preferences:
                preferences[organization_id] = await get_organization_preferences(
                    organization_id
                )
            prefs = preferences[organization_id]

            spec = routines.spec_from_model(routine)

            if spec.needs_apps and organization_id not in broken:
                broken[organization_id] = await db_client.apps_last_failing(
                    organization_id
                )

            decision = routines.decide(
                spec,
                now=now,
                zone=dnd.resolve_zone(getattr(prefs, "timezone", None)),
                business_hours=getattr(prefs, "business_hours", None),
                broken_apps=broken.get(organization_id, set()),
            )

            if decision.fire:
                # Stamped before the job is enqueued, not after. The stamp is
                # what stops a second firing, so a crash between the two must
                # leave a run that did not happen rather than one that happens
                # twice -- a duplicate morning report with different numbers
                # is worse than a missing one, which the next tick's MISSED
                # will at least name.
                await db_client.mark_routine_fired(routine.id, slot=decision.slot)
                await agent_timeline.record(
                    organization_id=organization_id,
                    kind=AgentEventKind.ROUTINE_FIRED.value,
                    actor=AgentEventActor.SYSTEM.value,
                    summary=f"{routine.name} started its scheduled run",
                    workflow_id=routine.workflow_id,
                    payload={
                        "routine_id": routine.id,
                        "slot": decision.slot.isoformat(),
                    },
                )
                await ctx["redis"].enqueue_job(
                    FunctionNames.RUN_AGENT_ROUTINE, routine.id
                )
                fired += 1
                continue

            if not decision.needs_attention:
                continue

            # Deduplicated against what the routine already says, so an
            # episode is one line rather than one a minute.
            reason = decision.reason.value if decision.reason else "unknown"
            if routine.last_skipped_reason == reason:
                continue

            await db_client.mark_routine_skipped(routine.id, reason=reason)
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.ROUTINE_SKIPPED.value,
                actor=AgentEventActor.SYSTEM.value,
                summary=f"{routine.name} did not run: {decision.detail}",
                workflow_id=routine.workflow_id,
                payload={"routine_id": routine.id, "reason": reason},
            )
        except Exception as exc:  # noqa: BLE001
            # One routine must never end the tick. Every other business's
            # routines would silently not run this minute, and nothing would say
            # which one caused it.
            logger.exception("Routine {} could not be evaluated: {}", routine.id, exc)

    if fired:
        logger.info("Started {} scheduled routine run(s)", fired)


async def run_agent_routine(_ctx, routine_id: int) -> None:
    """Do the run itself, off the tick."""
    from api.services.workflow.routine_runner import run_routine

    await run_routine(int(routine_id))
