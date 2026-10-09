"""What an agent can do in a huddle: read its own work, and propose.

Three reads that run at once and change nothing -- ``recent_work``,
``call_detail``, ``search_files`` -- and two writes that cannot reach a
customer: ``propose_edit`` (a draft and a card on the agent's thread, via
``self_edit.propose``; publishing is a click on that card, and nothing here
can do it) and ``remember`` (a note for the next huddle with this person,
see ``record``). There is no publish, settle or discard tool, and
``test_huddle.py`` fails if one is added.

Every read is scoped to the huddle's organisation *and* agent: a run id
from the model is checked against both before anything about it is said.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from api import constants
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import self_edit

RECENT_WORK = "recent_work"
CALL_DETAIL = "call_detail"
SEARCH_FILES = "search_files"
REMEMBER = "remember"
PROPOSE_EDIT = self_edit.TOOL_NAME

#: Run now and change nothing.
READS = frozenset({RECENT_WORK, CALL_DETAIL, SEARCH_FILES})
#: Change nothing a customer meets: a draft behind a card, or a huddle note.
PROPOSALS = frozenset({PROPOSE_EDIT, REMEMBER})

#: What counts as the agent's work on its thread.
WORK_KINDS = (
    AgentEventKind.CALL_ENDED.value,
    AgentEventKind.CALLER_WANTED.value,
    AgentEventKind.ESCALATED.value,
    AgentEventKind.OUTCOME_FILED.value,
    AgentEventKind.COULD_NOT.value,
    AgentEventKind.NEEDS_ATTENTION.value,
    AgentEventKind.ROUTINE_FIRED.value,
    AgentEventKind.ROUTINE_SKIPPED.value,
    AgentEventKind.DELIVERABLE.value,
    AgentEventKind.MESSAGE.value,
)
MAX_DAYS = 7
MAX_ITEMS = 15
#: Rows read for one answer: a busy agent's week is summarised, not listed.
MAX_ROWS = 300
MAX_DETAIL_ROWS = 40
MAX_PASSAGES = 4
MAX_PASSAGE_CHARS = 600
#: Payload fields worth saying about one event; never a transcript.
DETAIL_FIELDS = (
    "reason",
    "reason_code",
    "disposition",
    "answered",
    "duration_seconds",
    "test",
    "outcome",
    "error",
)


def schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": RECENT_WORK,
            "description": (
                "Your recent work: how many calls, chats and scheduled runs, "
                "how they ended (escalated, outcome filed, could not), and the "
                "latest few with their run ids and times. Runs now."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": (
                            "1 for today (the default), up to 7 for the last week."
                        ),
                    }
                },
            },
        },
        {
            "name": CALL_DETAIL,
            "description": (
                "What happened on one of your calls or runs, step by step, "
                "including why it escalated or failed. Give the run id from "
                "recent_work. Runs now."
            ),
            "parameters": {
                "type": "object",
                "properties": {"run_id": {"type": "integer"}},
                "required": ["run_id"],
            },
        },
        {
            "name": SEARCH_FILES,
            "description": (
                "Search the workspace's files for what they say about "
                "something. Returns passages with the file each came from. "
                "Runs now."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": REMEMBER,
            "description": (
                "Keep one short note for your next huddle with this person: "
                "how they want you to work with them, or a decision you two "
                "made. Never changes what you say to customers."
            ),
            "parameters": {
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
            },
        },
        {
            "name": PROPOSE_EDIT,
            "description": self_edit.DESCRIPTION
            + " In a huddle the person is your operator; propose only what "
            "they asked for.",
            "parameters": {
                "type": "object",
                "properties": self_edit.tool_properties(),
            },
        },
    ]


def _since(days: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    if days <= 1:
        zone = ZoneInfo(constants.DEFAULT_ORGANIZATION_TIMEZONE)
        local = now.astimezone(zone)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    return now - timedelta(days=days)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _is_work(row: Any) -> bool:
    """A message is work only when the agent sent it on a run: a reply in a
    chat it was answering, not its operator talking to it."""
    if row.kind != AgentEventKind.MESSAGE.value:
        return True
    return row.actor == AgentEventActor.AGENT.value and row.workflow_run_id is not None


async def recent_work(
    *, organization_id: int, workflow_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        days = int(arguments.get("days") or 1)
    except (TypeError, ValueError):
        days = 1
    days = max(1, min(MAX_DAYS, days))
    since = _since(days)
    rows = await db_client.agent_events(
        organization_id=organization_id,
        workflow_id=workflow_id,
        kinds=list(WORK_KINDS),
        limit=MAX_ROWS,
    )
    rows = [r for r in rows if _aware(r.at) >= since and _is_work(r)]
    counts = Counter(
        "replies" if r.kind == AgentEventKind.MESSAGE.value else r.kind for r in rows
    )
    calls = [r for r in rows if r.kind == AgentEventKind.CALL_ENDED.value]
    tests = sum(1 for r in calls if (r.payload or {}).get("test"))
    unanswered = sum(1 for r in calls if (r.payload or {}).get("answered") is False)
    return {
        "status": "ok",
        "period": "today" if days == 1 else f"last {days} days",
        "since": since.isoformat(),
        "counts": dict(counts),
        "calls": {
            "total": len(calls),
            "test_calls": tests,
            "not_answered": unanswered,
        },
        "latest": [
            {
                "run_id": r.workflow_run_id,
                "at": _aware(r.at).isoformat(),
                "kind": r.kind,
                "summary": r.summary,
            }
            for r in rows[:MAX_ITEMS]
        ],
        "truncated": len(rows) >= MAX_ROWS,
    }


async def call_detail(
    *, organization_id: int, workflow_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        run_id = int(arguments.get("run_id"))
    except (TypeError, ValueError):
        return {"status": "error", "error": "Give the run id from recent_work."}
    run = await db_client.get_workflow_run(run_id, organization_id=organization_id)
    if run is None or run.workflow_id != workflow_id:
        # Another agent's run, or another workspace's: not found either way.
        return {"status": "not_found", "error": f"Run {run_id} is not one of yours."}
    rows = await db_client.agent_events(
        organization_id=organization_id,
        workflow_id=workflow_id,
        workflow_run_id=run_id,
        limit=MAX_DETAIL_ROWS,
    )
    steps = []
    for row in rows:
        payload = row.payload or {}
        step: dict[str, Any] = {
            "at": _aware(row.at).isoformat(),
            "kind": row.kind,
            "summary": row.summary,
        }
        details = {k: payload[k] for k in DETAIL_FIELDS if k in payload}
        if details:
            step["details"] = details
        steps.append(step)
    return {
        "status": "ok",
        "run_id": run_id,
        "started_at": _aware(run.created_at).isoformat() if run.created_at else None,
        "state": getattr(run, "state", None),
        "steps": steps,
    }


async def search_files(
    *, organization_id: int, workflow_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    from api.services.workflow import files_search

    query = str(arguments.get("query") or "").strip()
    if not query:
        return {"status": "error", "error": "Say what to look for."}
    # The search itself, not ``for_thread``: that one writes a reading line
    # on Decibyl's thread, and a huddle is not Decibyl's conversation.
    result = await files_search.search(organization_id, query, limit=MAX_PASSAGES)
    passages = []
    for chunk in (result.get("chunks") or [])[:MAX_PASSAGES]:
        if not isinstance(chunk, dict):
            continue
        passages.append(
            {
                "from": chunk.get("citation")
                or chunk.get("document_name")
                or chunk.get("filename"),
                "text": str(chunk.get("text") or chunk.get("content") or "")[
                    :MAX_PASSAGE_CHARS
                ],
            }
        )
    out: dict[str, Any] = {"status": result.get("status") or "ok", "passages": passages}
    if result.get("instruction"):
        out["instruction"] = result["instruction"]
    return out


_READERS = {
    RECENT_WORK: recent_work,
    CALL_DETAIL: call_detail,
    SEARCH_FILES: search_files,
}


async def read(
    name: str, *, organization_id: int, workflow_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Run one read. Unknown names are answered, never raised."""
    reader = _READERS.get(name)
    if reader is None:
        return {"status": "error", "error": f"{name} is not one of your tools."}
    return await reader(
        organization_id=organization_id,
        workflow_id=workflow_id,
        arguments=arguments or {},
    )


async def propose_edit(
    *, organization_id: int, workflow_id: int, arguments: dict[str, Any]
) -> tuple[dict[str, Any], list[int]]:
    """The agent's change as a draft and a card on its own thread. Returns
    what the model is told and the ids of the cards written. Publishes
    nothing: ``self_edit.propose`` only ever saves a draft."""
    from api.services.workflow import agent_timeline

    with agent_timeline.collecting() as written:
        result = await self_edit.propose(
            organization_id=organization_id,
            workflow_id=workflow_id,
            workflow_run_id=None,
            arguments=arguments or {},
        )
    cards = [
        event_id
        for kind, event_id, _payload in written
        if kind == AgentEventKind.EDIT_PROPOSED.value
    ]
    return result, cards
