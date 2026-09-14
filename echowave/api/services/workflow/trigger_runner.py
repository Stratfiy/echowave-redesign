"""Running a trigger: one event in, one turn, a card at the end.

The routine runner's shape, on the routine runner's reasons (see
``routine_runner.py``): the text-chat engine so the bot behaves as it does
in a chat, and a record left either way because nobody is on the line to
hear it go wrong. What differs is the message -- the event's data and the
trigger's instruction rather than a standing instruction -- and the price:
one credit, decided in study §25.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind, WorkflowRunMode
from api.services.billing import events as billing_events
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow import agent_timeline, bot_triggers
from api.services.workflow.routine_runner import MAX_DELIVERABLE
from api.services.workflow.text_chat_runner import default_text_chat_checkpoint
from api.services.workflow.text_chat_session_service import (
    append_text_chat_user_message,
    default_text_chat_session_data,
    execute_pending_text_chat_turn,
    initialize_text_chat_session,
)


def _last_assistant_text(text_session: Any) -> str | None:
    turns = list((text_session.session_data or {}).get("turns") or [])
    if not turns:
        return None
    return (turns[-1].get("assistant_message") or {}).get("text")


async def run_trigger(
    trigger_id: int, payload: Any, event_id: str | None = None
) -> int | None:
    """Do one run for one event. Never raises.

    Returns the run id, or ``None`` when no run happened: the trigger
    vanished, or there was no credit. The filter is not re-checked here;
    the public route already declined anything the operator did not ask
    about, before the job was queued and before anything was charged.
    """
    trigger = await _load(trigger_id)
    if trigger is None:
        logger.warning("Trigger {} vanished before it could run", trigger_id)
        return None

    organization_id = trigger["organization_id"]
    workflow_id = trigger["workflow_id"]
    run_id: int | None = None

    try:
        workflow_run = await db_client.create_workflow_run(
            name=f"TRIGGER-{trigger['name'][:40]}",
            workflow_id=workflow_id,
            mode=WorkflowRunMode.TEXTCHAT.value,
            user_id=None,
            initial_context=None,
            use_draft=False,
            organization_id=organization_id,
        )
        run_id = workflow_run.id
        set_current_run_id(run_id)

        quota = await authorize_workflow_run_start(
            workflow_id=workflow_id,
            organization_id=organization_id,
            workflow_run_id=run_id,
        )
        if not quota.has_quota:
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.NEEDS_ATTENTION.value,
                actor=AgentEventActor.SYSTEM.value,
                summary=(
                    f"{trigger['name']} could not run: "
                    f"{quota.error_message or 'no credit for this run'}"
                ),
                workflow_id=workflow_id,
                workflow_run_id=run_id,
                payload={"trigger_id": trigger_id, "reason": "no_quota"},
            )
            return None

        message, missing = bot_triggers.run_message(
            name=trigger["name"],
            instruction=trigger["instruction"],
            fields=trigger["fields"],
            payload=payload,
        )
        await db_client.update_workflow_run(
            run_id,
            annotations={
                "trigger": {
                    "id": trigger_id,
                    "name": trigger["name"],
                    "event_id": event_id,
                    "missing_fields": missing,
                },
            },
        )

        text_session = await db_client.ensure_workflow_run_text_session(
            run_id,
            session_data=default_text_chat_session_data(),
            checkpoint=default_text_chat_checkpoint(),
        )
        text_session = await initialize_text_chat_session(
            run_id=run_id, text_session=text_session
        )
        text_session = await execute_pending_text_chat_turn(
            workflow_id=workflow_id, run_id=run_id, text_session=text_session
        )
        text_session = await append_text_chat_user_message(
            run_id=run_id,
            text_session=text_session,
            user_text=message,
            expected_revision=text_session.revision,
        )
        text_session = await execute_pending_text_chat_turn(
            workflow_id=workflow_id, run_id=run_id, text_session=text_session
        )

        answer = (_last_assistant_text(text_session) or "").strip()
        # One credit per run that passed the filter, whether or not the bot
        # had anything to say: the model ran and the tools were tried.
        await billing_events.charge_in_own_session(
            organization_id=organization_id,
            event=billing_events.TRIGGER_RUN,
            ref_id=str(run_id),
            note=trigger["name"][:80],
        )
        try:
            await db_client.mark_bot_trigger_fired(trigger_id)
        except Exception as exc:  # noqa: BLE001 - the stamp is bookkeeping
            logger.warning("Could not stamp trigger {}: {}", trigger_id, exc)

        if not answer:
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.COULD_NOT.value,
                summary=f"{trigger['name']} ran on an event and had nothing to report",
                workflow_id=workflow_id,
                workflow_run_id=run_id,
                payload={"trigger_id": trigger_id, "missing_fields": missing},
            )
            return run_id

        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.DELIVERABLE.value,
            summary=answer[:MAX_DELIVERABLE],
            workflow_id=workflow_id,
            workflow_run_id=run_id,
            payload={
                "trigger_id": trigger_id,
                "trigger": trigger["name"],
                "event_id": event_id,
                "missing_fields": missing,
                "credits": billing_events.credits_for(billing_events.TRIGGER_RUN),
                "billed_as": billing_events.TRIGGER_RUN,
            },
        )
        return run_id
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.exception("Trigger {} failed: {}", trigger_id, exc)
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.COULD_NOT.value,
            summary=f"{trigger['name']} could not finish its run",
            workflow_id=workflow_id,
            workflow_run_id=run_id,
            payload={"trigger_id": trigger_id, "error": str(exc)[:500]},
        )
        return run_id


async def _load(trigger_id: int) -> dict[str, Any] | None:
    """The trigger's fields, read once and detached (see routine_runner._load)."""
    from api.db.models import BotTriggerModel

    async with db_client.async_session() as session:
        trigger = await session.get(BotTriggerModel, trigger_id)
        if trigger is None:
            return None
        return {
            "id": trigger.id,
            "organization_id": trigger.organization_id,
            "workflow_id": trigger.workflow_id,
            "name": trigger.name,
            "instruction": trigger.instruction or "",
            "fields": list(trigger.fields or []),
        }


__all__ = ["run_trigger"]
