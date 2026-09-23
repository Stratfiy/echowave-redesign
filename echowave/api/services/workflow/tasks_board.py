"""The office's task board: one tracker people and bots both work from.

KAN-140 P1, and the answer to "can the bots coordinate?" A bot that needs a
colleague's help does not chat at it. It **files a task**: who it is for,
what is wanted, by when. The colleague runs it as one text turn with the
brief as its instruction, posts the result on the card, and the bot that
asked is told in its own chat and carries on. A person files a task the
same way, and can be the assignee: a task with no bot on it is the team's,
and the team marks it done on the board.

Why a board and not a message. A message between two bots is a hand-off
nobody can see, and the first time it goes wrong nobody can say where. A
task is a row with a status, an asker and an owner. It is the Zapier
"call an agent" shape (the caller waits for a result) with the wait made
visible, and the Lindy "society" shape with a person able to see every
step.

Rules:

- **Depth.** A task filed by a task run is one deeper. Past ``MAX_DEPTH``
  the bot is told to ask a person instead. Two bots handing a task back
  and forth forever is not coordination.
- **Never to yourself.** A bot cannot assign a task to itself.
- **One credit per task run**, as a trigger run: one instruction, one turn.
- **Everything is a row.** Filing, starting and finishing each write to the
  board, to Decibyl's thread, and to the threads of the bots involved.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline, one_shot_run

TOOL_NAME = "create_task"
DESCRIPTION = (
    "File a task on the team's board for a colleague bot (by @handle) or for "
    "the team (assignee 'team'), when a job is theirs rather than yours: a "
    "follow-up in three days, a record another bot keeps, a check a person "
    "must make. Give a short title and a brief they can act on without you. "
    "You will not get the result in this turn; say you have filed it and "
    "end your reply. Never file a task to yourself."
)

# The columns, on paperclip's issue model (TB-1): backlog -> todo ->
# in_progress -> in_review -> done, with blocked and cancelled as the side
# exits. ``blocked`` is what the old ``waiting`` and ``could_not`` both were:
# work a person has to do something about, with the reason on the card.
BACKLOG = "backlog"
TODO = "todo"
IN_PROGRESS = "in_progress"
IN_REVIEW = "in_review"
DONE = "done"
BLOCKED = "blocked"
CANCELLED = "cancelled"
STATUSES = (BACKLOG, TODO, IN_PROGRESS, IN_REVIEW, DONE, BLOCKED, CANCELLED)
#: The five the board had before TB-1, under their new names. With the flag
#: off these are the only columns a person can move a card to.
LEGACY_STATUSES = (TODO, IN_PROGRESS, BLOCKED, DONE, CANCELLED)
TERMINAL = (DONE, CANCELLED)
#: Work a person has to do something about.
NEEDS_A_PERSON = (BLOCKED, IN_REVIEW)
# The old names, for the callers that still say them.
DOING = IN_PROGRESS
WAITING = BLOCKED
COULD_NOT = BLOCKED

PRIORITIES = ("critical", "high", "medium", "low")
DEFAULT_PRIORITY = "medium"


def enabled() -> bool:
    from api.services import features

    return features.is_on("task_board")


def landing_status(answer: str) -> str:
    """Where an agent's finished task goes. On the board (TB-1) an answer
    waits in review for a person to sign off; off it, done as before. No
    answer is blocked either way: somebody has to look."""
    if not answer:
        return BLOCKED
    return IN_REVIEW if enabled() else DONE


def identifier_prefix(organization_name: str | None) -> str:
    """``Decibyl`` -> ``DEC``, ``Kovai Fertility Centre`` -> ``KFC``: the
    letters a person would use, never a number the database chose."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", organization_name or "") if w]
    if len(words) >= 2:
        prefix = "".join(w[0] for w in words[:3])
    elif words:
        prefix = words[0][:3]
    else:
        prefix = "T"
    return prefix.upper()


def identifier(prefix: str | None, number: int | None) -> str | None:
    if number is None:
        return None
    return f"{prefix or 'T'}-{number}"


TEAM = "team"
MAX_DEPTH = 2
MAX_TITLE = 200
MAX_BRIEF = 4_000
MAX_RESULT = 2_000

#: How much of a delegate's result reaches the bot that asked.
#:
#: **The summary rule.** A delegate returns a summary; its raw tool output
#: stays in its own run. The bot that filed the task is a coordinator, and
#: a coordinator that receives forty CRM rows from one colleague and a
#: ledger from another has a context full of other bots' working and no
#: room for its own. So the result is asked for as a colleague's report,
#: cut to a few lines here regardless of what the model did, and stripped
#: of anything that reads as pasted output: a code block, a table, a JSON
#: blob. The full result stays on the task card, where a person reads it,
#: and the tool results stay on the delegate's run, where the timeline
#: shows them.
MAX_SUMMARY = 600
MAX_SUMMARY_LINES = 6


def tool_properties() -> dict[str, Any]:
    return {
        "title": {"type": "string", "description": "Two to eight words."},
        "brief": {
            "type": "string",
            "description": (
                "What is wanted and everything the assignee needs to do it: "
                "names, numbers, dates, what to say."
            ),
        },
        "assignee": {
            "type": "string",
            "description": "A bot's @handle, or 'team' for a person.",
        },
        "due": {
            "type": "string",
            "description": "When it is needed, if it matters: an ISO date or 'in 3 days'.",
        },
        "priority": {
            "type": "string",
            "enum": list(PRIORITIES),
            "description": "How urgent, if not medium.",
        },
    }


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": tool_properties(),
            "required": ["title", "brief", "assignee"],
        },
    }


class TaskError(ValueError):
    """The board cannot take that; the message says why, for the screen."""


def _match_bot(wanted: str, roster: list[Any]) -> Any | None:
    key = wanted.strip().lstrip("@").casefold()
    if not key:
        return None
    hits = [
        w
        for w in roster
        if (getattr(w, "handle", None) or "").casefold() == key
        or (getattr(w, "name", None) or "").casefold() == key
    ]
    return hits[0] if len(hits) == 1 else None


def parse_due(text: str | None, *, now: datetime | None = None) -> datetime | None:
    """An ISO date, or 'in N days/hours', or nothing. Never raises."""
    from datetime import timedelta

    raw = (text or "").strip().lower()
    if not raw:
        return None
    now = now or datetime.now(UTC)
    parts = raw.split()
    if len(parts) == 3 and parts[0] == "in" and parts[1].isdigit():
        n = int(parts[1])
        if parts[2].startswith("day"):
            return now + timedelta(days=n)
        if parts[2].startswith("hour"):
            return now + timedelta(hours=n)
        if parts[2].startswith("week"):
            return now + timedelta(weeks=n)
    try:
        parsed = datetime.fromisoformat(raw.replace("z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _depth_of(workflow_run_id: int | None) -> int:
    if not workflow_run_id:
        return 0
    try:
        run = await db_client.get_workflow_run(workflow_run_id)
    except Exception:  # noqa: BLE001
        return 0
    task = ((getattr(run, "annotations", None) or {}).get("task") or {}) if run else {}
    return int(task.get("depth") or 0) + 1 if task else 0


def as_dict(
    task: Any,
    names: dict[int, str] | None = None,
    *,
    people: dict[int, str] | None = None,
    prefix: str | None = None,
    comment_count: int | None = None,
) -> dict[str, Any]:
    names = names or {}
    people = people or {}
    assignee_user_id = getattr(task, "assignee_user_id", None)
    blocked_by = getattr(task, "blocked_by", None)
    return {
        "id": task.id,
        "number": getattr(task, "number", None),
        "identifier": identifier(prefix, getattr(task, "number", None)),
        "title": task.title,
        "brief": task.brief or "",
        "status": task.status,
        "priority": getattr(task, "priority", None) or DEFAULT_PRIORITY,
        "assignee_user_id": assignee_user_id,
        "assignee_user_name": people.get(assignee_user_id)
        if assignee_user_id
        else None,
        "parent_id": getattr(task, "parent_id", None),
        "blocked_by": [int(x) for x in blocked_by]
        if isinstance(blocked_by, list)
        else [],
        "comment_count": comment_count,
        "from_workflow_id": task.from_workflow_id,
        "from_name": names.get(task.from_workflow_id)
        if task.from_workflow_id
        else None,
        "assignee_workflow_id": task.assignee_workflow_id,
        "assignee_name": (
            names.get(task.assignee_workflow_id) if task.assignee_workflow_id else None
        ),
        "created_by": task.created_by,
        "depth": int(task.depth or 0),
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "result": task.result,
        "workflow_run_id": task.workflow_run_id,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


# --- filing -----------------------------------------------------------------


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ids(value: Any) -> list[int] | None:
    if not isinstance(value, list):
        return None
    out = [i for i in (_int_or_none(v) for v in value) if i is not None]
    return out or None


async def create(
    *,
    organization_id: int,
    from_workflow_id: int | None,
    workflow_run_id: int | None,
    arguments: dict[str, Any],
    created_by: int | None = None,
) -> dict[str, Any]:
    """File a task. Returns what the model (or the screen) is told. Never
    raises on the model's mistakes; those come back as a reason."""
    title = " ".join(str(arguments.get("title") or "").split())[:MAX_TITLE]
    brief = str(arguments.get("brief") or "").strip()[:MAX_BRIEF]
    wanted = str(arguments.get("assignee") or TEAM).strip()
    if not title:
        return {"status": "not_filed", "reason": "Give the task a title."}
    if not brief:
        return {"status": "not_filed", "reason": "Say what is wanted, in the brief."}
    priority = str(arguments.get("priority") or DEFAULT_PRIORITY).strip().lower()
    if priority not in PRIORITIES:
        return {
            "status": "not_filed",
            "reason": f"Priority is one of {', '.join(PRIORITIES)}.",
        }
    # A person as the owner (TB-1): the screen says ``user:<id>``; a model
    # never does, it names a bot or the team.
    assignee_user_id: int | None = None
    if wanted.lower().startswith("user:"):
        try:
            assignee_user_id = int(wanted.split(":", 1)[1])
        except ValueError:
            return {"status": "not_filed", "reason": "That is not a person here."}
        wanted = TEAM

    roster = list(
        await db_client.get_all_workflows_for_listing(organization_id=organization_id)
    )
    names = {w.id: w.name for w in roster}
    assignee = None
    if wanted.lstrip("@").casefold() != TEAM:
        assignee = _match_bot(wanted, roster)
        if assignee is None:
            return {
                "status": "not_filed",
                "reason": f"No bot called {wanted!r} here; use its exact @handle, or 'team'.",
            }
        if from_workflow_id is not None and assignee.id == from_workflow_id:
            return {
                "status": "not_filed",
                "reason": "You cannot file a task to yourself.",
            }

    depth = await _depth_of(workflow_run_id)
    if depth > MAX_DEPTH:
        return {
            "status": "not_filed",
            "reason": (
                "This is already a task about a task about a task. Ask a person "
                "on the team instead, with ask_for_decision."
            ),
        }

    task = await db_client.create_task(
        organization_id=organization_id,
        title=title,
        brief=brief,
        from_workflow_id=from_workflow_id,
        assignee_workflow_id=assignee.id if assignee else None,
        created_by=created_by,
        source_run_id=workflow_run_id,
        depth=depth,
        due_at=parse_due(arguments.get("due")),
        priority=priority,
        assignee_user_id=assignee_user_id,
        parent_id=_int_or_none(arguments.get("parent_id")),
        blocked_by=_ids(arguments.get("blocked_by")),
        status=BACKLOG if arguments.get("backlog") and enabled() else TODO,
    )

    owner = (
        f"@{getattr(assignee, 'handle', None) or assignee.name}"
        if assignee
        else "a person"
        if assignee_user_id
        else "the team"
    )
    asker = names.get(from_workflow_id, "A person") if from_workflow_id else "A person"
    line = f"Task for {owner}: {title}"
    payload = {
        "task": as_dict(task, names),
        "from": "Decibyl" if from_workflow_id is None else None,
    }
    # On Decibyl's thread, where the whole office is read.
    await agent_timeline.record_activity(
        organization_id=organization_id,
        summary=f"{asker} filed a task for {owner}: {title}",
        payload=payload,
        in_channel=False,
    )
    # On the asker's own thread, so its chat shows what it delegated.
    if from_workflow_id is not None:
        await agent_timeline.record_activity(
            organization_id=organization_id,
            summary=line,
            workflow_id=from_workflow_id,
            workflow_run_id=workflow_run_id,
            payload=payload,
            in_channel=False,
        )

    if assignee is not None:
        from api.tasks.arq import enqueue_job
        from api.tasks.function_names import FunctionNames

        try:
            await enqueue_job(FunctionNames.RUN_AGENT_TASK, task.id)
        except Exception as exc:  # noqa: BLE001 - on the board either way
            logger.error("Task {} filed but could not be started: {}", task.id, exc)
            await db_client.update_task(
                task.id, organization_id=organization_id, status=WAITING
            )
            return {
                "status": "filed",
                "task_id": task.id,
                "note": (
                    f"Filed for {owner}, but it could not be started just now; "
                    "it is on the board as waiting. Say so and end your reply."
                ),
            }
        return {
            "status": "filed",
            "task_id": task.id,
            "note": (
                f"Filed for {owner}. They will do it and the result will reach "
                "you in your chat. Say you have filed it and end your reply."
            ),
        }
    return {
        "status": "filed",
        "task_id": task.id,
        "note": (
            "Filed for the team; a person will see it on the board. Say you have "
            "filed it and end your reply."
        ),
    }


# --- doing ------------------------------------------------------------------


def run_message(*, title: str, brief: str, asker: str) -> str:
    return "\n".join(
        [
            f"A task from {asker}: {title}",
            "",
            brief,
            "",
            (
                "Do it now with what you have and your tools. Reply with the "
                "result as you would report it to a colleague: what you did, "
                "what you found, anything they must know, in a few lines. "
                "Report, do not paste: no raw rows, records, tables or tool "
                "output -- those stay on your own run, and only your summary "
                "reaches them. If you truly cannot, say why in one line. Do "
                "not file this task back to them."
            ),
        ]
    )


_FENCE = re.compile(r"```.*?```", re.DOTALL)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_RULE = re.compile(r"^\s*[-=_*]{3,}\s*$")
_RECORD = re.compile(r"^\s*[\[{].*[\]}]\s*,?\s*$")
_KEY_VALUE_ROW = re.compile(r'^\s*"?[A-Za-z_][A-Za-z0-9_ ]*"?\s*[:=]\s*\S.*$')


def summarise_for_asker(result: str) -> str:
    """The delegate's result as the asker hears it: a report, never a dump.

    Drops what reads as pasted output -- fenced blocks, table rows, JSON
    records, runs of ``key: value`` lines -- keeps the prose, and cuts the
    rest to a few lines. Says when it cut, so the asker knows the card
    has more rather than believing the summary is all there was.
    """
    text = _FENCE.sub(" ", result or "")
    kept: list[str] = []
    key_value_run = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            key_value_run = 0
            continue
        if (
            _TABLE_ROW.match(stripped)
            or _RULE.match(stripped)
            or _RECORD.match(stripped)
        ):
            continue
        if _KEY_VALUE_ROW.match(stripped) and not stripped.endswith((".", "?", "!")):
            # One "Status: done" is a report. Six in a row is a record.
            key_value_run += 1
            if key_value_run > 2:
                if kept and kept[-1] and _KEY_VALUE_ROW.match(kept[-1]):
                    kept.pop()
                if len(kept) >= 2 and _KEY_VALUE_ROW.match(kept[-1]):
                    kept.pop()
                continue
        else:
            key_value_run = 0
        kept.append(stripped)

    cut = len(kept) > MAX_SUMMARY_LINES
    lines = kept[:MAX_SUMMARY_LINES]
    summary = "\n".join(lines)
    if len(summary) > MAX_SUMMARY:
        cut = True
        head = summary[:MAX_SUMMARY]
        stop = max(head.rfind(". "), head.rfind("\n"))
        summary = head[: stop + 1] if stop > MAX_SUMMARY // 2 else head
    summary = summary.strip()
    if not summary:
        return "Done; the details are on the task card."
    if cut or len(summary) < len((result or "").strip()) - 80:
        summary += " (More on the task card.)"
    return summary


def _tool_calls_in(turns: list[dict[str, Any]]) -> int:
    """How many tool results the delegate's run holds -- the raw output
    that stays with it rather than travelling to the asker."""
    count = 0
    for turn in turns:
        for event in turn.get("events") or []:
            if isinstance(event, dict) and event.get("type") == "tool_call_result":
                count += 1
    return count


async def run_task(task_id: int) -> int | None:
    """The assignee does the task as one text turn. Never raises."""
    from pipecat.utils.run_context import set_current_run_id

    from api.db.models import AgentTaskModel
    from api.enums import WorkflowRunMode
    from api.services.billing import events as billing_events
    from api.services.quota_service import authorize_workflow_run_start
    from api.services.workflow.routine_runner import MAX_DELIVERABLE
    from api.services.workflow.text_chat_runner import default_text_chat_checkpoint
    from api.services.workflow.text_chat_session_service import (
        append_text_chat_user_message,
        default_text_chat_session_data,
        execute_pending_text_chat_turn,
        initialize_text_chat_session,
    )

    async with db_client.async_session() as session:
        row = await session.get(AgentTaskModel, task_id)
        if row is None or row.assignee_workflow_id is None:
            return None
        task = as_dict(row)
        organization_id = row.organization_id
    assignee_id = int(task["assignee_workflow_id"])
    from_id = task["from_workflow_id"]

    roster = list(
        await db_client.get_all_workflows_for_listing(organization_id=organization_id)
    )
    names = {w.id: w.name for w in roster}
    handles = {w.id: getattr(w, "handle", None) for w in roster}
    asker = f"@{handles.get(from_id) or names.get(from_id)}" if from_id else "the team"
    assignee_name = names.get(assignee_id, "the bot")

    await db_client.update_task(task_id, organization_id=organization_id, status=DOING)
    run_id: int | None = None
    try:
        workflow_run = await db_client.create_workflow_run(
            name=f"TASK-{task['title'][:40]}",
            workflow_id=assignee_id,
            mode=WorkflowRunMode.TEXTCHAT.value,
            user_id=None,
            initial_context=None,
            use_draft=False,
            organization_id=organization_id,
        )
        run_id = workflow_run.id
        set_current_run_id(run_id)
        quota = await authorize_workflow_run_start(
            workflow_id=assignee_id,
            organization_id=organization_id,
            workflow_run_id=run_id,
        )
        if not quota.has_quota:
            # Nothing was tried, so nothing failed: the task waits for a
            # person to put credit on the account and press Run again. Filing
            # it as could-not put it in Done, beside the finished ones, which
            # is the one column where nobody looks for work still to do.
            reason = quota.error_message or "no credit for this run"
            await _finish(
                task_id,
                organization_id=organization_id,
                status=BLOCKED,
                result=f"Could not start: {reason}",
                run_id=run_id,
                from_id=from_id,
                assignee_name=assignee_name,
                title=task["title"],
            )
            return None
        await db_client.update_workflow_run(
            run_id,
            annotations={
                "task": {"id": task_id, "title": task["title"], "depth": task["depth"]}
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
            workflow_id=assignee_id, run_id=run_id, text_session=text_session
        )
        text_session = await append_text_chat_user_message(
            run_id=run_id,
            text_session=text_session,
            user_text=run_message(
                title=task["title"], brief=task["brief"], asker=asker
            ),
            expected_revision=text_session.revision,
        )
        text_session = await execute_pending_text_chat_turn(
            workflow_id=assignee_id, run_id=run_id, text_session=text_session
        )
        turns = list((text_session.session_data or {}).get("turns") or [])
        answer = (
            (turns[-1].get("assistant_message") or {}).get("text") if turns else ""
        ) or ""
        answer = answer.strip()[:MAX_DELIVERABLE]
        used = _tool_calls_in(turns)
        if used:
            # The raw output stays here, on the delegate's own run, and the
            # timeline says so -- a person who wants the rows opens this run,
            # and the asker never carried them.
            await agent_timeline.record_activity(
                organization_id=organization_id,
                summary=(
                    f"{assignee_name} used {used} tool call"
                    f"{'s' if used != 1 else ''} for the task; the full output "
                    "stays on this run"
                ),
                workflow_id=assignee_id,
                workflow_run_id=run_id,
                payload={"task_id": task_id, "tool_calls": used},
                in_channel=False,
            )
        await billing_events.charge_in_own_session(
            organization_id=organization_id,
            event=billing_events.TASK_RUN,
            ref_id=str(run_id),
            note=task["title"][:80],
            workflow_id=assignee_id,
        )
        await _finish(
            task_id,
            organization_id=organization_id,
            status=landing_status(answer),
            result=answer or "Ran and had nothing to report.",
            run_id=run_id,
            from_id=from_id,
            assignee_name=assignee_name,
            title=task["title"],
        )
        return run_id
    except Exception as exc:  # noqa: BLE001 - the board must say something
        logger.exception("Task {} failed: {}", task_id, exc)
        await _finish(
            task_id,
            organization_id=organization_id,
            status=BLOCKED,
            result=f"Could not finish: {str(exc)[:300]}",
            run_id=run_id,
            from_id=from_id,
            assignee_name=assignee_name,
            title=task["title"],
        )
        return run_id
    finally:
        await one_shot_run.close(run_id)


async def _finish(
    task_id: int,
    *,
    organization_id: int,
    status: str,
    result: str,
    run_id: int | None,
    from_id: int | None,
    assignee_name: str,
    title: str,
) -> None:
    """Write the result on the card, tell the asker, note it in the office."""
    task = await db_client.update_task(
        task_id,
        organization_id=organization_id,
        status=status,
        result=result[:MAX_RESULT],
        workflow_run_id=run_id,
    )
    done = status in (DONE, IN_REVIEW)
    verb = (
        "handed over for review"
        if status == IN_REVIEW
        else "finished"
        if done
        else "could not start"
        if result.startswith("Could not start")
        else "could not do"
    )
    line = f"{assignee_name} {verb} the task: {title}"
    payload = {
        "task": as_dict(task) if task else {"id": task_id},
        "result": result[:500],
    }
    if enabled() and task is not None:
        # The report is a line on the card, under the agent's name, so the
        # person reviewing reads the conversation, not one overwritten field.
        try:
            await db_client.add_task_comment(
                organization_id=organization_id,
                task_id=task_id,
                body=result[:MAX_RESULT],
                author_workflow_id=task.assignee_workflow_id,
            )
        except Exception as exc:  # noqa: BLE001 - the result column has it
            logger.warning("Could not note the result on task {}: {}", task_id, exc)
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.DELIVERABLE.value
        if done
        else AgentEventKind.COULD_NOT.value,
        actor=AgentEventActor.AGENT.value,
        summary=f"{line}\n{result[:MAX_DELIVERABLE_LINE]}",
        workflow_id=task.assignee_workflow_id if task else None,
        workflow_run_id=run_id,
        payload=payload,
        in_channel=False,
    )
    await agent_timeline.record_activity(
        organization_id=organization_id,
        summary=line,
        payload=payload,
        in_channel=False,
    )
    if from_id is not None:
        # The asker hears the result as a line in its own chat and answers
        # through the normal path: the hand-off completes where it began.
        from api.tasks.arq import enqueue_job
        from api.tasks.function_names import FunctionNames

        # The summary, not the result: the asker is a coordinator, and its
        # context carries the colleague's report, never the colleague's rows.
        report = (
            f"Task result from {assignee_name} ({title}): {summarise_for_asker(result)}"
        )
        try:
            await enqueue_job(
                FunctionNames.ANSWER_CHANNEL_MESSAGE, from_id, None, report
            )
        except Exception as exc:  # noqa: BLE001 - the card carries it regardless
            logger.warning(
                "Could not hand the result of task {} back: {}", task_id, exc
            )


MAX_DELIVERABLE_LINE = 600


# --- the person's half ------------------------------------------------------


async def set_status(
    *,
    organization_id: int,
    task_id: int,
    status: str,
    result: str | None,
    user_id: int,
) -> dict[str, Any]:
    if status not in (STATUSES if enabled() else LEGACY_STATUSES):
        raise TaskError("Not a column on this board.")
    task = await db_client.get_task(task_id, organization_id=organization_id)
    if task is None:
        raise TaskError("That task is not here.")
    fields: dict[str, Any] = {"status": status}
    if result is not None:
        fields["result"] = result.strip()[:MAX_RESULT]
    if (
        status == TODO
        and task.assignee_workflow_id is not None
        and task.status in TERMINAL + NEEDS_A_PERSON
    ):
        # Re-queued for a bot: run it again.
        updated = await db_client.update_task(
            task_id, organization_id=organization_id, **fields
        )
        from api.tasks.arq import enqueue_job
        from api.tasks.function_names import FunctionNames

        try:
            await enqueue_job(FunctionNames.RUN_AGENT_TASK, task_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Task {} could not be re-run: {}", task_id, exc)
        return as_dict(updated)
    updated = await db_client.update_task(
        task_id, organization_id=organization_id, **fields
    )
    if status in TERMINAL:
        word = (
            "signed off"
            if status == DONE and task.status == IN_REVIEW
            else "done"
            if status == DONE
            else "cancelled"
        )
        await agent_timeline.record_activity(
            organization_id=organization_id,
            summary=f"A person marked the task {word}: {task.title}",
            payload={"task": as_dict(updated), "by": user_id},
            in_channel=False,
        )
    return as_dict(updated)


# --- the card's fields and comments (TB-1) ----------------------------------

EDITABLE = ("priority", "assignee", "parent_id", "blocked_by", "due")


async def edit(
    *,
    organization_id: int,
    task_id: int,
    changes: dict[str, Any],
    user_id: int,
) -> dict[str, Any]:
    """Change what a card says about itself: priority, owner, parent, what
    it waits on, when it is due. Never its status; that is a move."""
    task = await db_client.get_task(task_id, organization_id=organization_id)
    if task is None:
        raise TaskError("That task is not here.")
    fields: dict[str, Any] = {}
    if "priority" in changes:
        priority = str(changes["priority"] or DEFAULT_PRIORITY).lower()
        if priority not in PRIORITIES:
            raise TaskError(f"Priority is one of {', '.join(PRIORITIES)}.")
        fields["priority"] = priority
    if "assignee" in changes:
        wanted = str(changes["assignee"] or TEAM).strip()
        if wanted.lower().startswith("user:"):
            fields["assignee_user_id"] = _int_or_none(wanted.split(":", 1)[1])
            if fields["assignee_user_id"] is None:
                raise TaskError("That is not a person here.")
            fields["assignee_workflow_id"] = None
        elif wanted.lstrip("@").casefold() == TEAM:
            fields["assignee_user_id"] = None
            fields["assignee_workflow_id"] = None
        else:
            roster = list(
                await db_client.get_all_workflows_for_listing(
                    organization_id=organization_id
                )
            )
            bot = _match_bot(wanted, roster)
            if bot is None:
                raise TaskError(f"No bot called {wanted!r} here.")
            fields["assignee_workflow_id"] = bot.id
            fields["assignee_user_id"] = None
    if "parent_id" in changes:
        parent = _int_or_none(changes["parent_id"])
        if parent == task_id:
            raise TaskError("A task cannot be its own parent.")
        fields["parent_id"] = parent
    if "blocked_by" in changes:
        ids = _ids(changes["blocked_by"]) or []
        fields["blocked_by"] = [i for i in ids if i != task_id] or None
    if "due" in changes:
        fields["due_at"] = parse_due(changes["due"]) if changes["due"] else None
    if not fields:
        return as_dict(task)
    updated = await db_client.update_task(
        task_id, organization_id=organization_id, **fields
    )
    return as_dict(updated)


MAX_COMMENT = 4_000


async def comment(
    *,
    organization_id: int,
    task_id: int,
    body: str,
    user_id: int,
) -> dict[str, Any]:
    text = body.strip()[:MAX_COMMENT]
    if not text:
        raise TaskError("Say something.")
    task = await db_client.get_task(task_id, organization_id=organization_id)
    if task is None:
        raise TaskError("That task is not here.")
    row = await db_client.add_task_comment(
        organization_id=organization_id,
        task_id=task_id,
        body=text,
        author_user_id=user_id,
    )
    return comment_dict(row)


def comment_dict(
    row: Any,
    names: dict[int, str] | None = None,
    people: dict[int, str] | None = None,
) -> dict[str, Any]:
    names = names or {}
    people = people or {}
    return {
        "id": row.id,
        "task_id": row.task_id,
        "body": row.body,
        "author_user_id": row.author_user_id,
        "author_workflow_id": row.author_workflow_id,
        "author_name": (
            names.get(row.author_workflow_id)
            if row.author_workflow_id
            else people.get(row.author_user_id)
            if row.author_user_id
            else None
        ),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


__all__ = [
    "BACKLOG",
    "BLOCKED",
    "CANCELLED",
    "COULD_NOT",
    "DESCRIPTION",
    "DOING",
    "DONE",
    "IN_PROGRESS",
    "IN_REVIEW",
    "LEGACY_STATUSES",
    "MAX_DEPTH",
    "NEEDS_A_PERSON",
    "PRIORITIES",
    "STATUSES",
    "TEAM",
    "TODO",
    "TOOL_NAME",
    "WAITING",
    "comment",
    "edit",
    "enabled",
    "identifier",
    "identifier_prefix",
    "landing_status",
    "TaskError",
    "as_dict",
    "create",
    "parse_due",
    "run_message",
    "run_task",
    "set_status",
    "tool_properties",
    "tool_schema",
]
