"""Decibyl keeps working (D-1a).

A reply from Decibyl may take a handful of tool rounds and then must answer;
the cap is what stops a model going in circles on the account's credit. But
a real job -- read the inbox, find the lead, look up the order, draft the
reply -- does not fit in six, and the old ending was "ask again from there",
which put the job's bookkeeping on the person.

Now the turn **hands the rest to the board**. When the cap is hit with the
model still mid-plan, the reply says so and files a task the person can see,
with the whole transcript and tool state so far as its ``continuation``. A
worker picks it up and carries on from exactly there: more rounds under a
larger ceiling, a progress line on the thread every few steps, the
workspace's spend cap (S-1) checked as it goes, and the answer landing on
the thread and on the card when it is done. Nobody is asked to ask again.

Three rules carry this module.

**The task is Decibyl's, not a bot's.** It has no assignee and no run; it
is the same conversation, continued. The board shows it beside a bot's tasks
because the board is where unfinished work lives, whoever holds it.

**Every continuation is bounded twice.** ``DECIBYL_TASK_MAX_ROUNDS`` is the
ceiling on steps; the workspace's budget policy is the ceiling on money. At
either, the model is asked to answer with what it has, and the card says
which ceiling was reached.

**Nothing here runs until ``DECIBYL_LONG_TASKS_ENABLED`` is on.** Off, the
turn ends at the cap exactly as it did.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api import constants
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline, tasks_board

#: What the person reads when the turn hands the rest to the board.
HANDED_OFF = (
    "I'm still on it. I've taken {rounds} steps so far and will keep going "
    "in the background; the result will land on this thread and on the "
    "board as task #{task_id}."
)

#: Said to the model when a continuation reaches a ceiling.
FINISH_NOW = (
    "You have reached the limit for this task ({why}). Answer now with what "
    "you have: what you found, what you did, and what is still left, so the "
    "person can decide what to do next."
)

RAN_OUT = "ran out of steps"
OVER_BUDGET = "the workspace's spend cap"

TITLE_MAX = 200


def enabled() -> bool:
    return constants.DECIBYL_LONG_TASKS_ENABLED


def title_for(request: str) -> str:
    """The card's title: the ask, on one line, as the person wrote it."""
    line = " ".join((request or "").split())
    return (line[: TITLE_MAX - 1] + "…") if len(line) > TITLE_MAX else line or "Decibyl"


def _current_helper() -> str | None:
    from api.services.helpers import turn as helper_turn

    return helper_turn.current()


async def hand_off(
    organization_id: int,
    *,
    messages: list[dict[str, Any]],
    loaded: dict[str, dict[str, Any]],
    preset: str | None,
    thread_id: str | None,
    author_id: int | None,
    request: str,
    rounds: int,
) -> Any:
    """File the rest of a turn on the board and queue it. Returns the task."""
    task = await db_client.create_task(
        organization_id=organization_id,
        title=title_for(request),
        brief=request[:4000],
        status=tasks_board.TODO,
        from_workflow_id=None,
        assignee_workflow_id=None,
        created_by=author_id,
        depth=0,
        continuation={
            "messages": messages,
            "loaded": loaded,
            "preset": preset,
            "thread_id": thread_id,
            "author_id": author_id,
            "request": request,
            "rounds": rounds,
            "handed_off_at": datetime.now(UTC).isoformat(),
            # The helper the turn ran as carries on with it (launch stream
            # `agents`): the same instructions and the same narrowed tools.
            "helper": _current_helper(),
        },
    )
    await agent_timeline.record_activity(
        organization_id=organization_id,
        summary=f"Decibyl is continuing in the background: {task.title}",
        payload={"task": tasks_board.as_dict(task)},
        in_channel=False,
        thread_id=thread_id,
    )
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    try:
        await enqueue_job(FunctionNames.RUN_AGENT_TASK, task.id)
    except Exception as exc:  # noqa: BLE001 - the card exists; Run again picks it up
        logger.error("Could not queue Decibyl's task {}: {}", task.id, exc)
    return task


def is_continuation(task: Any) -> bool:
    return bool(getattr(task, "continuation", None))


async def _progress(
    organization_id: int, *, thread_id: str | None, rounds: int, last: list[str]
) -> None:
    named = ", ".join(dict.fromkeys(n for n in last if n)) or "thinking"
    await agent_timeline.record_activity(
        organization_id=organization_id,
        summary=f"Still on it: {rounds} steps so far (last: {named})",
        payload={"rounds": rounds, "last": last},
        in_channel=False,
        thread_id=thread_id,
    )


async def _over_budget(organization_id: int) -> str | None:
    """The spend cap's refusal, or None while there is room."""
    from api.services.billing import budgets, reservations

    verdict = await budgets.evaluate_in_own_session(
        organization_id=organization_id, workflow_id=None
    )
    if not verdict.allowed:
        return verdict.message
    try:
        async with db_client.async_session() as session:
            if not await reservations.has_credit(
                session, organization_id=organization_id
            ):
                return "the account is out of credit"
    except Exception as exc:  # noqa: BLE001 - fail open, as the run gate does
        logger.error("Could not read credit for org {}: {}", organization_id, exc)
    return None


async def continue_task(task_id: int) -> None:
    """Carry a handed-off turn on to its answer. Never raises."""
    from api.services.agent_builder import client, settings
    from api.services.workflow import decibyl

    task = None
    try:
        async with db_client.async_session() as session:
            from api.db.models import AgentTaskModel

            task = await session.get(AgentTaskModel, task_id)
        if task is None or not is_continuation(task):
            return
        organization_id = int(task.organization_id)
        state = dict(task.continuation or {})
        thread_id = state.get("thread_id")
        if not enabled():
            await _finish(
                task,
                status=tasks_board.WAITING,
                result="Could not continue: background work is switched off here.",
                thread_id=thread_id,
            )
            return

        refusal = await _over_budget(organization_id)
        if refusal:
            await _finish(
                task,
                status=tasks_board.WAITING,
                result=f"Could not continue: {refusal}",
                thread_id=thread_id,
            )
            return

        await db_client.update_task(
            task_id, organization_id=organization_id, status=tasks_board.DOING
        )

        conversation = client.Conversation(messages=list(state.get("messages") or []))
        loaded: dict[str, dict[str, Any]] = dict(state.get("loaded") or {})
        rounds = int(state.get("rounds") or 0)
        author_id = state.get("author_id")
        request = str(state.get("request") or "")
        ceiling = rounds + constants.DECIBYL_TASK_MAX_ROUNDS
        every = max(1, constants.DECIBYL_TASK_PROGRESS_EVERY)

        from api.services.helpers import turn as helper_turn

        with (
            agent_timeline.in_thread(thread_id),
            helper_turn.running_as(state.get("helper")),
        ):
            async with db_client.async_session() as session:
                model = await settings.resolve_for_organization(
                    session, state.get("preset"), organization_id=organization_id
                )
            tools = await decibyl.tools_for(organization_id, loaded)
            reply = await decibyl._speak(
                model, conversation, organization_id, tools=tools
            )
            stopped_by: str | None = None
            since_progress = 0
            while reply.wants_tools:
                rounds += 1
                since_progress += 1
                conversation.add_assistant(reply)
                reads_only = True
                asked_for_schema = False
                names: list[str] = []
                for call in reply.tool_calls:
                    names.append(str(call.name))
                    if call.name == decibyl.connected_tools.LOAD_TOOL_NAME:
                        result = await decibyl._load_tool(organization_id, call, loaded)
                        asked_for_schema = True
                    else:
                        result = await decibyl._tool(
                            organization_id,
                            call,
                            author_id,
                            request=request,
                            thread_id=thread_id,
                        )
                    conversation.add_tool_result(call, result)
                    if not decibyl._was_a_read(call, result):
                        reads_only = False
                if asked_for_schema:
                    tools = await decibyl.tools_for(organization_id, loaded)
                if since_progress >= every:
                    since_progress = 0
                    await _progress(
                        organization_id, thread_id=thread_id, rounds=rounds, last=names
                    )
                    refusal = await _over_budget(organization_id)
                    if refusal:
                        stopped_by = OVER_BUDGET
                if not reads_only:
                    # A card ended the tool phase: the model says what it
                    # proposed and stops, the same rule as in the turn.
                    reply = await decibyl._speak(
                        model, conversation, organization_id, tools=None
                    )
                    break
                if stopped_by is None and rounds >= ceiling:
                    stopped_by = RAN_OUT
                if stopped_by is not None:
                    conversation.add_user(FINISH_NOW.format(why=stopped_by))
                    reply = await decibyl._speak(
                        model, conversation, organization_id, tools=None
                    )
                    break
                reply = await decibyl._speak(
                    model, conversation, organization_id, tools=tools
                )

            body = (reply.text or "").strip()
            if not body:
                body = (
                    "I could not finish that in the background and have nothing "
                    "more to report. Ask again and I will start from what is on "
                    "this card."
                )
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.MESSAGE.value,
                actor=AgentEventActor.AGENT.value,
                summary=body[:500],
                payload={
                    "body": body,
                    "from": decibyl.NAME,
                    "preset": state.get("preset"),
                    "task_id": task_id,
                    "rounds": rounds,
                },
                in_channel=False,
            )
            await decibyl.reply_draft.clear(organization_id)

        from api.services.billing import events as billing_events

        await billing_events.charge_in_own_session(
            organization_id=organization_id,
            event=billing_events.TASK_RUN,
            ref_id=f"decibyl-task:{task_id}",
            note=task.title[:80],
        )
        status = tasks_board.DONE if stopped_by is None else tasks_board.COULD_NOT
        note = (
            f"\n\n(Stopped: {stopped_by}, after {rounds} steps.)" if stopped_by else ""
        )
        await _finish(
            task,
            status=status,
            result=f"{body}{note}",
            thread_id=thread_id,
            rounds=rounds,
        )
    except Exception as exc:  # noqa: BLE001 - the card must say something
        logger.error("Decibyl could not continue task {}: {}", task_id, exc)
        if task is not None:
            try:
                await _finish(
                    task,
                    status=tasks_board.COULD_NOT,
                    result="I could not continue this in the background. This is "
                    "us, not you; press Run again or ask on the thread.",
                    thread_id=(task.continuation or {}).get("thread_id"),
                )
            except Exception as inner:  # noqa: BLE001
                logger.error("...and could not record that either: {}", inner)


async def _finish(
    task: Any,
    *,
    status: str,
    result: str,
    thread_id: str | None,
    rounds: int | None = None,
) -> None:
    fields: dict[str, Any] = {
        "status": status,
        "result": result[: tasks_board.MAX_RESULT],
    }
    if status in tasks_board.TERMINAL + (tasks_board.BLOCKED,):
        # The transcript has done its job; the card keeps the answer, not
        # the working. A task put back to To do starts from the ask.
        state = dict(task.continuation or {})
        fields["continuation"] = {k: v for k, v in state.items() if k != "messages"} | {
            "messages": [],
            "rounds": rounds or state.get("rounds") or 0,
            "done": True,
        }
    updated = await db_client.update_task(
        task.id, organization_id=task.organization_id, **fields
    )
    verb = (
        "finished"
        if status == tasks_board.DONE
        else "could not start"
        if result.startswith("Could not start")
        else "could not finish"
    )
    await agent_timeline.record_activity(
        organization_id=task.organization_id,
        summary=f"Decibyl {verb} the task: {task.title}",
        payload={"task": tasks_board.as_dict(updated) if updated else {"id": task.id}},
        in_channel=False,
        thread_id=thread_id,
    )
