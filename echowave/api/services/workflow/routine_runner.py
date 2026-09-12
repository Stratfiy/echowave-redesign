"""Running a Desk: one turn, no caller, a card at the end.

A scheduled bot has nobody on the line, which sounds like it should make this
simpler and does the opposite. A live call has a caller who hears when it goes
wrong; a Desk that fails produces silence, and silence is what the operator
was already getting before they hired it. So the run's only real obligation is
to leave a record either way -- a deliverable when it worked, a ``COULD_NOT``
when it did not -- and that obligation is the whole of the error handling
here.

The engine is the text-chat path, the same one the eval runner drives: the
bot's own prompts, tools, connectors and model, minus audio. That is not a
shortcut. A Desk that ran on a second, simpler runtime would answer
differently from the same bot in a chat, and the first time those two
disagreed nobody would be able to say which was the bot's real behaviour.

The routine's instruction arrives as the one user message. Which means a
routine is editable without touching the bot's prompt, and two Desks can share
one bot and differ only in what they are told to do each morning.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind, WorkflowRunMode
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow import agent_timeline
from api.services.workflow.text_chat_runner import default_text_chat_checkpoint
from api.services.workflow.text_chat_session_service import (
    append_text_chat_user_message,
    default_text_chat_session_data,
    execute_pending_text_chat_turn,
    initialize_text_chat_session,
)

#: What the deliverable carries of the bot's reply. Long enough for a morning
#: summary, short enough that the card is a card.
MAX_DELIVERABLE = 2_000


def _last_assistant_text(text_session: Any) -> Optional[str]:
    turns = list((text_session.session_data or {}).get("turns") or [])
    if not turns:
        return None
    return (turns[-1].get("assistant_message") or {}).get("text")


async def run_routine(routine_id: int) -> None:
    """Do one run of one routine. Never raises.

    Never raises because the caller is a cron tick serving every tenant: one
    routine throwing would end the tick, and every other business's Desk would
    silently not run that minute. A failure here is this routine's failure and
    is recorded as this routine's failure.
    """
    routine = await _load(routine_id)
    if routine is None:
        logger.warning("Routine {} vanished before it could run", routine_id)
        return

    organization_id = routine["organization_id"]
    workflow_id = routine["workflow_id"]
    run_id: Optional[int] = None

    try:
        workflow_run = await db_client.create_workflow_run(
            name=f"ROUTINE-{routine['name'][:40]}",
            workflow_id=workflow_id,
            mode=WorkflowRunMode.TEXTCHAT.value,
            user_id=None,
            initial_context=None,
            # The published bot, not the draft. A Desk running somebody's
            # half-finished edit every morning is how an unreviewed change
            # reaches real accounting software -- the eval runner uses the
            # draft precisely because a person is sitting there watching it.
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
            # Recorded as something the operator must act on, not swallowed.
            # A Desk that stops because the balance ran out looks exactly like
            # a Desk that is broken, and the difference is one they can fix.
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.NEEDS_ATTENTION.value,
                actor=AgentEventActor.SYSTEM.value,
                summary=(
                    f"{routine['name']} could not run: "
                    f"{quota.error_message or 'no credit for this run'}"
                ),
                workflow_id=workflow_id,
                workflow_run_id=run_id,
                payload={"routine_id": routine_id, "reason": "no_quota"},
            )
            return

        await db_client.update_workflow_run(
            run_id,
            annotations={
                "routine": {"id": routine_id, "name": routine["name"]},
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
        # The opening turn. A bot's greeting is written for a caller and is not
        # the deliverable, so its output is deliberately thrown away -- but it
        # has to happen, because the graph's first node is what loads the
        # bot's context and tools.
        text_session = await execute_pending_text_chat_turn(
            workflow_id=workflow_id, run_id=run_id, text_session=text_session
        )

        text_session = await append_text_chat_user_message(
            run_id=run_id,
            text_session=text_session,
            user_text=routine["instruction"] or routine["name"],
            expected_revision=text_session.revision,
        )
        text_session = await execute_pending_text_chat_turn(
            workflow_id=workflow_id, run_id=run_id, text_session=text_session
        )

        answer = (_last_assistant_text(text_session) or "").strip()
        if not answer:
            # Ran, produced nothing. The single most important case to record:
            # a blank run and a run that never happened look identical from
            # the outside, and one of them means the instruction is wrong.
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.COULD_NOT.value,
                summary=f"{routine['name']} ran and had nothing to report",
                workflow_id=workflow_id,
                workflow_run_id=run_id,
                payload={"routine_id": routine_id},
            )
            return

        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.DELIVERABLE.value,
            summary=answer[:MAX_DELIVERABLE],
            workflow_id=workflow_id,
            workflow_run_id=run_id,
            payload={"routine_id": routine_id, "routine": routine["name"]},
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.exception("Routine {} failed: {}", routine_id, exc)
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.COULD_NOT.value,
            summary=f"{routine['name']} could not finish its run",
            workflow_id=workflow_id,
            workflow_run_id=run_id,
            payload={"routine_id": routine_id, "error": str(exc)[:500]},
        )


async def _load(routine_id: int) -> Optional[dict[str, Any]]:
    """The routine's fields, read once and detached.

    A dict rather than the model because everything after this point awaits
    other sessions, and a lazy load off a closed one is the failure that
    reaches production looking like the routine itself is broken.
    """
    from api.db.models import AgentRoutineModel

    async with db_client.async_session() as session:
        routine = await session.get(AgentRoutineModel, routine_id)
        if routine is None:
            return None
        return {
            "id": routine.id,
            "organization_id": routine.organization_id,
            "workflow_id": routine.workflow_id,
            "name": routine.name,
            "instruction": routine.instruction,
        }


__all__ = ["MAX_DELIVERABLE", "run_routine"]
