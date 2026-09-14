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
from typing import Optional

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
    """Do the run itself, off the tick, then process it like any other run.

    The second half is the part that was missing. ``learn_from_run`` — gaps,
    confirmations, everything the business finds out about itself — hangs off
    ``PROCESS_WORKFLOW_COMPLETION``, and that was enqueued from exactly two
    places, both of them voice: the pipecat teardown and the telephony status
    processor. So a routine could fail to reach the same system every morning
    for a month and the organisation learned nothing from it.

    Nothing about the machinery was voice-specific — ``learn_from_run`` takes a
    ``workflow_run_id``, an intent and a set of interactions, and none of those
    imply a phone. Only the wiring was. AGENTS.md names this exact shape:
    ``CallDirection`` has four values, two of them ring no phone, and "anything
    that assumes 'agent' means 'call' is a bug waiting to happen".

    Costing is idempotent — ``cost_workflow_run`` skips a run with a
    ``costed_at`` — so this cannot double-charge. It costs these runs promptly
    instead of leaving them to the settlement backstop, which is a second small
    improvement rather than a risk.
    """
    from api.services.workflow.routine_runner import run_routine

    run_id = await run_routine(int(routine_id))
    if run_id is None:
        # No run happened: the routine vanished, or there was no credit for it.
        # There is nothing to cost and nothing to learn from.
        return

    await _ctx["redis"].enqueue_job(
        FunctionNames.PROCESS_WORKFLOW_COMPLETION, int(run_id)
    )


async def answer_channel_message(
    _ctx, workflow_id: int, folder_id: Optional[int], text: str
) -> None:
    """One bot answers one thing somebody said in a channel, then the run is
    processed like any other.

    The second half matters as much as the first. Until the routine wiring
    landed, PROCESS_WORKFLOW_COMPLETION was enqueued only from the two voice
    paths -- so a bot that could not answer a question in a channel would have
    left no gap for the business to see, which is the whole point of asking it
    there.
    """
    from api.services.workflow.channel_reply import answer_in_channel

    run_id = await answer_in_channel(
        int(workflow_id), int(folder_id) if folder_id is not None else None, text
    )
    if run_id is None:
        # No run happened: the bot vanished, or there was no credit. Nothing to
        # cost and nothing to learn from.
        return

    await _ctx["redis"].enqueue_job(
        FunctionNames.PROCESS_WORKFLOW_COMPLETION, int(run_id)
    )
    # Housekeeping for the channel the bot just spoke in, after the reply and
    # as its own job: folding the thread's oldest end into its summary is a
    # model call, and the person waiting for the answer should not wait on it.
    # Handed this run so the fold runs on the bot's own LLM and bills against
    # it -- see services/workflow/channel_context.py.
    if folder_id is not None:
        await _ctx["redis"].enqueue_job(
            FunctionNames.COMPACT_CHANNEL_CONTEXT, int(folder_id), int(run_id)
        )


async def compact_channel_context(_ctx, folder_id: int, run_id: int) -> None:
    """Fold a channel's oldest unsummarised messages into its rolling précis.

    A no-op until enough has accumulated above the watermark; see
    ``channel_context.COMPACT_AFTER``. Never raises: a fold that fails leaves
    the channel exactly as it was.
    """
    from api.services.workflow.channel_context import compact

    organization_id = await db_client.get_organization_id_by_workflow_run_id(
        int(run_id)
    )
    if organization_id is None:
        return
    await compact(
        organization_id=int(organization_id),
        folder_id=int(folder_id),
        run_id=int(run_id),
    )
