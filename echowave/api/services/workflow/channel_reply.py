"""A bot answering something somebody said in a channel.

The other half of `@mentions`. `routes/agent_timeline.post_message` records
what a person said and enqueues one of these per bot they addressed; this runs
the bot's turn and puts its answer back in the same channel.

**Never raises.** The caller is an arq job serving every tenant, and a bot that
throws here would take the worker with it. A failure is this bot's failure and
is recorded as this bot's failure -- which matters more than usual on this
path, because the person who typed the message is sitting there watching for a
reply. Silence is the one outcome that must not happen: it is
indistinguishable from being ignored.

The run is a real ``workflow_run`` in TEXTCHAT mode rather than a lighter
thing, which buys three properties that would otherwise each need building:
the quota check that stops a bot answering on an empty balance, the costing
that bills the turn, and -- since the routine wiring landed -- the learning
pass, so a question this bot could not answer in a channel becomes a gap the
business can see.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind, WorkflowRunMode
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow import agent_timeline, channel_context
from api.services.workflow.text_chat_runner import default_text_chat_checkpoint
from api.services.workflow.text_chat_session_service import (
    append_text_chat_user_message,
    default_text_chat_session_data,
    execute_pending_text_chat_turn,
    initialize_text_chat_session,
)

#: What the channel carries of a reply. A bot answering in a thread is writing
#: a message, not a report; anything past this is a document and belongs behind
#: a deliverable card rather than inline.
MAX_REPLY = 4_000


def _last_assistant_text(text_session: Any) -> Optional[str]:
    turns = list((text_session.session_data or {}).get("turns") or [])
    if not turns:
        return None
    return (turns[-1].get("assistant_message") or {}).get("text")


async def answer_in_channel(
    workflow_id: int, folder_id: int, text: str
) -> Optional[int]:
    """Have one bot answer one message. Returns the run id, or None.

    None means no run happened — the bot vanished, or there was no credit for
    it — and the caller should not put it through post-run processing.
    """
    workflow = await db_client.get_workflow_by_id(workflow_id)
    if workflow is None:
        logger.warning("Workflow {} vanished before it could answer", workflow_id)
        return None

    organization_id = workflow.organization_id
    name = workflow.name or f"workflow {workflow_id}"

    try:
        workflow_run = await db_client.create_workflow_run(
            name=f"CHANNEL-{name[:40]}",
            workflow_id=workflow_id,
            mode=WorkflowRunMode.TEXTCHAT.value,
            user_id=None,
            initial_context=None,
            # The published bot, not the draft. Somebody's half-finished edit
            # answering a colleague in a channel is the same unreviewed change
            # reaching a real conversation that routines refuse for.
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
            # Said in the channel, not swallowed into a log. A bot that stops
            # because the balance ran out looks exactly like one that is
            # broken, and the difference is the one thing the operator can fix.
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.NEEDS_ATTENTION.value,
                actor=AgentEventActor.SYSTEM.value,
                summary=(
                    f"{name} could not answer: "
                    f"{quota.error_message or 'no credit for this run'}"
                ),
                workflow_id=workflow_id,
                workflow_run_id=run_id,
                folder_id=folder_id,
            )
            return None

        text_session = await db_client.ensure_workflow_run_text_session(
            run_id,
            session_data=default_text_chat_session_data(),
            checkpoint=default_text_chat_checkpoint(),
        )
        text_session = await initialize_text_chat_session(
            run_id=run_id, text_session=text_session
        )
        # The opening turn is a greeting written for a caller, and nobody in a
        # channel wants to be greeted before their question is answered. Its
        # output is discarded, but it has to run: the graph's first node is
        # what loads the bot's context and tools.
        text_session = await execute_pending_text_chat_turn(
            workflow_id=workflow_id, run_id=run_id, text_session=text_session
        )

        # The channel's own conversation, in front of the question.
        #
        # This is the whole of "a bot in a channel knows what is going on in
        # it": what people asked, what they corrected, and what other bots
        # already did here -- three context sources, one read, no new table
        # and no permissions screen, because being filed in the channel is the
        # permission. See services/workflow/channel_context.py.
        #
        # Read here rather than at enqueue time so it is the thread as it
        # stands when the bot actually answers: two bots addressed in one
        # message run as two jobs, and the second should see the first's
        # reply rather than a snapshot from before either had spoken.
        thread = await channel_context.recent_thread(
            organization_id=organization_id, folder_id=folder_id
        )
        text_session = await append_text_chat_user_message(
            run_id=run_id,
            text_session=text_session,
            # One message rather than two turns: a separate context turn would
            # be a turn the bot answers, and the person is waiting for a reply
            # to what they actually asked. The question goes last so it is the
            # most recent thing in the window.
            user_text=f"{thread}\n\n{text}" if thread else text,
            expected_revision=text_session.revision,
        )
        text_session = await execute_pending_text_chat_turn(
            workflow_id=workflow_id, run_id=run_id, text_session=text_session
        )

        answer = (_last_assistant_text(text_session) or "").strip()
        if not answer:
            # The single most important case to record. A bot that ran and
            # produced nothing looks exactly like a bot that never ran, and one
            # of those means the question needs asking differently.
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.COULD_NOT.value,
                summary=f"{name} had nothing to say to that",
                workflow_id=workflow_id,
                workflow_run_id=run_id,
                folder_id=folder_id,
            )
            return run_id

        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary=answer[:MAX_REPLY],
            workflow_id=workflow_id,
            workflow_run_id=run_id,
            folder_id=folder_id,
            payload={"body": answer[:MAX_REPLY], "in_reply_to": text[:500]},
        )
        return run_id
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.exception(
            "Workflow {} could not answer in channel: {}", workflow_id, exc
        )
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.COULD_NOT.value,
            summary=f"{name} could not finish answering that",
            workflow_id=workflow_id,
            folder_id=folder_id,
            payload={"error": str(exc)[:500]},
        )
        return None


__all__ = ["MAX_REPLY", "answer_in_channel"]
