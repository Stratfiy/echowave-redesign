"""``team_activity``: what the other agents in this workspace did (item 4).

A "chief of staff" agent has to answer "what did the team do this week, and
what did it cost?" and until now it could only ask each agent in turn, or
guess. This is the read: one tool, one line per agent -- runs and how they
ended, files drafted, approvals waiting, credits and tokens spent -- then
the failures, in words short enough to act on.

Read-only, and held to four rules that are the whole point of it:

* **Same workspace only.** Every query below filters on ``organization_id``
  in SQL (runs reach it through ``workflows``), and the workspace comes
  from the run, never from the model's arguments. There is no argument that
  names another one.
* **Offered only to agents whose owner said so.** ``can_see_team`` in the
  agent's own configuration, off unless it is plainly ``true``, and behind
  the ``team_activity`` flag. One gate for the schema and the handler
  (``offered``), so the model is never shown a tool nothing answers.
* **Never more than the team's own record.** Only rows the timeline would
  show without asking (``visibility = always``), and nothing addressed to
  one person (``private_to``, or a card with an owner of its own). A
  caller's words and one person's private cards are not the team's.
* **Compact.** One line per agent and a capped failure list, cut to
  ``MAX_TOKENS`` with the cut said out loud -- a model that is not told the
  list was shortened will report the short list as the whole team.

Usage is read through ``usage_by_agent`` and nothing else, so the day agent
runs are metered in ``model_usage`` that one function changes and the tool
does not. Until then credits come from the credit ledger, which stamps every
debit with the agent that made it, and tokens from
``workflow_runs.usage_info``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import db_client
from api.db.models import (
    AgentEventModel,
    CreditLedgerModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import (
    AgentEventKind,
    AgentEventVisibility,
    CreditLedgerKind,
    WorkflowStatus,
)
from api.services import features, prompt_budget
from api.services.billing.credits import PAISE_PER_CREDIT
from api.services.workflow import actions
from api.services.workflow.outcome_board import codes_on

TOOL_NAME = "team_activity"
FEATURE = "team_activity"
#: The agent's own setting, in ``workflow_configurations``. Stored there
#: rather than in a column for the reason ``notify_on`` is: no migration.
CONFIG_KEY = "can_see_team"

RANGES = ("today", "7d", "30d")
DEFAULT_RANGE = "7d"
_DAYS = {"7d": 7, "30d": 30}
#: The workspace's day is the Indian one, as in the budgets.
IST = timezone(timedelta(hours=5, minutes=30))

#: The most the answer may cost the model. Tokens are estimated the way the
#: rest of the prompt is (``prompt_budget.estimate``).
MAX_TOKENS = 1200
#: Failures listed, and the words kept of each.
MAX_FAILURES = 8
MAX_FAILURE_CHARS = 140
#: Files named per agent before "+N more".
MAX_FILES_PER_AGENT = 3
#: One agent's line, at most.
MAX_AGENT_LINE_CHARS = 420
#: Rows read for the file, failure and approval lists, and runs read for
#: outcomes and tokens. Counts of runs and credits are exact sums in SQL;
#: these bound the rows that have to be opened one by one.
MAX_EVENT_ROWS = 600
MAX_APPROVAL_ROWS = 200
MAX_RUNS_SCANNED = 5_000

DESCRIPTION = (
    "See what the other agents in this workspace did: per agent, the runs "
    "and how they ended, files and documents drafted (with ids), approvals "
    "waiting, and the credits and tokens spent, then the failures with a "
    "short reason. Read-only, this workspace only. Use it to brief the "
    "owner or to spot an agent that is stuck or costly; name an agent to "
    "look at one."
)


def tool_properties() -> dict[str, Any]:
    return {
        "range": {
            "type": "string",
            "enum": list(RANGES),
            "description": (
                "today (since midnight, India time), 7d or 30d. Default "
                f"{DEFAULT_RANGE}."
            ),
        },
        "agent": {
            "type": "string",
            "description": "Only this agent, by name. Omit for the whole team.",
        },
    }


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": tool_properties(),
            "required": [],
        },
    }


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FEATURE, organization_id)


def wants_team(configurations: Mapping[str, Any] | None) -> bool:
    """Whether an owner turned "Can see the team" on. Only a plain ``true``:
    a string or a number in that key is somebody's half-finished edit, not
    consent to read the rest of the workspace."""
    if not isinstance(configurations, Mapping):
        return False
    return configurations.get(CONFIG_KEY) is True


def offered(organization_id: int | None, configurations: Any) -> bool:
    """The one gate, for the schema and for the handler."""
    return enabled(organization_id) and wants_team(configurations)


def window_start(range_name: str, now: datetime) -> datetime:
    """Where ``range_name`` begins, in UTC."""
    if range_name == "today":
        local = now.astimezone(IST)
        return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    return now - timedelta(days=_DAYS[range_name])


# ---------------------------------------------------------------------------
# Usage: the one function that knows where spend and tokens are kept.
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    credits: int = 0
    tokens: int = 0
    #: True when the run scan hit its cap, so ``tokens`` is a floor.
    tokens_partial: bool = False


def _whole(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def tokens_in(usage_info: Any) -> int:
    """Tokens one run recorded. ``usage_info`` is free-form JSON written by
    several versions of the pipeline, so anything unreadable is zero for that
    run and costs nothing else."""
    if not isinstance(usage_info, Mapping):
        return 0
    llm = usage_info.get("llm")
    if not isinstance(llm, Mapping):
        return 0
    total = 0
    for entry in llm.values():
        if not isinstance(entry, Mapping):
            continue
        counted = _whole(entry.get("total_tokens"))
        total += counted or (
            _whole(entry.get("prompt_tokens")) + _whole(entry.get("completion_tokens"))
        )
    return total


async def usage_by_agent(
    session: AsyncSession,
    *,
    organization_id: int,
    workflow_ids: list[int],
    since: datetime,
) -> dict[int, Usage]:
    """Credits and tokens each agent used since ``since``.

    The only place the tool learns about spend. Today: credits from the
    credit ledger (stamped with the agent that made each debit) and tokens
    from ``workflow_runs.usage_info``. Agent runs are not metered in
    ``model_usage`` yet; when they are, this is the function that changes.
    """
    usage: dict[int, Usage] = {wid: Usage() for wid in workflow_ids}
    if not workflow_ids:
        return usage

    spent = await session.execute(
        select(
            CreditLedgerModel.workflow_id,
            func.coalesce(func.sum(-CreditLedgerModel.delta_paise), 0),
        )
        .where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.USAGE.value,
            CreditLedgerModel.workflow_id.in_(workflow_ids),
            CreditLedgerModel.created_at >= since,
        )
        .group_by(CreditLedgerModel.workflow_id)
    )
    for workflow_id, paise in spent.all():
        usage[int(workflow_id)].credits = max(0, int(paise)) // PAISE_PER_CREDIT

    runs = await session.execute(
        select(WorkflowRunModel.workflow_id, WorkflowRunModel.usage_info)
        .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
        .where(
            WorkflowModel.organization_id == organization_id,
            WorkflowRunModel.workflow_id.in_(workflow_ids),
            WorkflowRunModel.created_at >= since,
        )
        .order_by(WorkflowRunModel.id.desc())
        .limit(MAX_RUNS_SCANNED + 1)
    )
    rows = runs.all()
    partial = len(rows) > MAX_RUNS_SCANNED
    for workflow_id, usage_info in rows[:MAX_RUNS_SCANNED]:
        usage[int(workflow_id)].tokens += tokens_in(usage_info)
    if partial:
        for entry in usage.values():
            entry.tokens_partial = True
    return usage


# ---------------------------------------------------------------------------
# The rest of the picture
# ---------------------------------------------------------------------------


@dataclass
class AgentRow:
    workflow_id: int
    name: str
    runs: int = 0
    completed: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    reports: int = 0
    approvals: list[str] = field(default_factory=list)
    failures: int = 0
    usage: Usage = field(default_factory=Usage)

    @property
    def active(self) -> bool:
        return bool(
            self.runs
            or self.files
            or self.reports
            or self.approvals
            or self.failures
            or self.usage.credits
            or self.usage.tokens
        )


@dataclass
class Failure:
    at: datetime
    name: str
    reason: str


def _shows_to_team(row_payload: Any) -> bool:
    """False for a row addressed to one person."""
    payload = row_payload if isinstance(row_payload, Mapping) else {}
    return not any(
        payload.get(key) is not None
        for key in ("private_to", "only_user_id", "owner_user_id", "requested_by")
    )


async def _agents(
    session: AsyncSession, *, organization_id: int, exclude_id: int | None
) -> tuple[list[AgentRow], int]:
    """The agents this answer may cover, and how many admin-only ones it left
    out (said in the header, never dropped silently)."""
    query = select(
        WorkflowModel.id, WorkflowModel.name, WorkflowModel.visibility
    ).where(
        WorkflowModel.organization_id == organization_id,
        WorkflowModel.status == WorkflowStatus.ACTIVE.value,
    )
    if exclude_id is not None:
        query = query.where(WorkflowModel.id != exclude_id)
    rows = (await session.execute(query.order_by(WorkflowModel.id))).all()
    shown = [
        AgentRow(workflow_id=int(wid), name=str(name or f"Agent {wid}"))
        for wid, name, visibility in rows
        if (visibility or "everyone") == "everyone"
    ]
    return shown, len(rows) - len(shown)


async def _runs(
    session: AsyncSession,
    *,
    organization_id: int,
    rows: dict[int, AgentRow],
    since: datetime,
) -> bool:
    """Run counts (exact) and outcome codes (from the newest runs). Returns
    whether the outcome scan was cut short."""
    ids = list(rows)
    counted = await session.execute(
        select(
            WorkflowRunModel.workflow_id,
            func.count(WorkflowRunModel.id),
            func.count(WorkflowRunModel.id).filter(
                WorkflowRunModel.is_completed.is_(True)
            ),
        )
        .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
        .where(
            WorkflowModel.organization_id == organization_id,
            WorkflowRunModel.workflow_id.in_(ids),
            WorkflowRunModel.created_at >= since,
        )
        .group_by(WorkflowRunModel.workflow_id)
    )
    for workflow_id, total, done in counted.all():
        rows[int(workflow_id)].runs = int(total)
        rows[int(workflow_id)].completed = int(done)

    scanned = await session.execute(
        select(WorkflowRunModel.workflow_id, WorkflowRunModel.annotations)
        .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
        .where(
            WorkflowModel.organization_id == organization_id,
            WorkflowRunModel.workflow_id.in_(ids),
            WorkflowRunModel.created_at >= since,
        )
        .order_by(WorkflowRunModel.id.desc())
        .limit(MAX_RUNS_SCANNED + 1)
    )
    found = scanned.all()
    for workflow_id, notes in found[:MAX_RUNS_SCANNED]:
        for code in set(codes_on(notes)):
            row = rows[int(workflow_id)]
            row.outcomes[code] = row.outcomes.get(code, 0) + 1
    return len(found) > MAX_RUNS_SCANNED


def _file_names(payload: Any) -> list[str]:
    """``name (id)`` for each file a deliverable carries."""
    if not isinstance(payload, Mapping):
        return []
    out: list[str] = []
    attachments = payload.get("attachments")
    if isinstance(attachments, list):
        for item in attachments:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("filename") or "file").strip()
            ident = str(item.get("document_uuid") or "").strip()
            out.append(f"{name} ({ident})" if ident else name)
    return out


def _image_names(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    images = payload.get("images")
    if not isinstance(images, list):
        return []
    out: list[str] = []
    for item in images:
        if isinstance(item, Mapping):
            ident = str(item.get("image_uuid") or item.get("id") or "").strip()
            if ident:
                out.append(f"image {ident}")
    return out


async def _events(
    session: AsyncSession,
    *,
    organization_id: int,
    rows: dict[int, AgentRow],
    since: datetime,
) -> list[Failure]:
    """Files drafted and failures, from the timeline, as the team may see it."""
    private_to = AgentEventModel.payload["private_to"].as_string()
    result = await session.execute(
        select(
            AgentEventModel.id,
            AgentEventModel.workflow_id,
            AgentEventModel.kind,
            AgentEventModel.summary,
            AgentEventModel.payload,
            AgentEventModel.at,
        )
        .where(
            AgentEventModel.organization_id == organization_id,
            AgentEventModel.workflow_id.in_(list(rows)),
            AgentEventModel.at >= since,
            AgentEventModel.visibility == AgentEventVisibility.ALWAYS.value,
            private_to.is_(None),
            AgentEventModel.kind.in_(
                [
                    AgentEventKind.COULD_NOT.value,
                    AgentEventKind.DELIVERABLE.value,
                    AgentEventKind.IMAGES_MADE.value,
                ]
            ),
        )
        .order_by(AgentEventModel.at.desc(), AgentEventModel.id.desc())
        .limit(MAX_EVENT_ROWS)
    )
    failures: list[Failure] = []
    for event_id, workflow_id, kind, summary, payload, at in result.all():
        row = rows.get(int(workflow_id))
        if row is None or not _shows_to_team(payload):
            continue
        if kind == AgentEventKind.COULD_NOT.value:
            row.failures += 1
            failures.append(
                Failure(
                    at=at,
                    name=row.name,
                    reason=prompt_budget.clip(
                        " ".join(str(summary or "").split()) or "no reason recorded",
                        MAX_FAILURE_CHARS,
                    ),
                )
            )
        elif kind == AgentEventKind.IMAGES_MADE.value:
            row.files.extend(_image_names(payload) or [f"images (event {event_id})"])
        else:
            named = _file_names(payload)
            if named:
                row.files.extend(named)
            else:
                row.reports += 1
    return failures


async def _approvals(
    session: AsyncSession, *, organization_id: int, rows: dict[int, AgentRow]
) -> None:
    """Cards still waiting on a person, now, whatever the range: a card from
    last month that nobody answered is still waiting."""
    state = func.coalesce(AgentEventModel.payload.op("->>")("state"), actions.PROPOSED)
    private_to = AgentEventModel.payload["private_to"].as_string()
    result = await session.execute(
        select(AgentEventModel.workflow_id, AgentEventModel.payload)
        .where(
            AgentEventModel.organization_id == organization_id,
            AgentEventModel.workflow_id.in_(list(rows)),
            AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
            AgentEventModel.visibility == AgentEventVisibility.ALWAYS.value,
            private_to.is_(None),
            state == actions.PROPOSED,
        )
        .order_by(AgentEventModel.id.desc())
        .limit(MAX_APPROVAL_ROWS)
    )
    for workflow_id, payload in result.all():
        if not _shows_to_team(payload):
            continue
        label = str((payload or {}).get("action") or "action")
        rows[int(workflow_id)].approvals.append(label)


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------


def _count_label(items: list[str]) -> str:
    counted: dict[str, int] = defaultdict(int)
    for item in items:
        counted[item] += 1
    return ", ".join(
        f"{name} x{n}" if n > 1 else name for name, n in sorted(counted.items())
    )


def _thousands(number: int) -> str:
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k" if number < 10_000 else f"{number // 1_000}k"
    return str(number)


def agent_line(row: AgentRow) -> str:
    parts: list[str] = []
    if row.runs:
        outcome = ""
        if row.outcomes:
            top = sorted(row.outcomes.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            outcome = "; outcomes " + ", ".join(f"{code} {n}" for code, n in top)
        parts.append(f"{row.runs} runs ({row.completed} finished){outcome}")
    else:
        parts.append("no runs")
    if row.failures:
        parts.append(f"{row.failures} could not")
    if row.files:
        shown = row.files[:MAX_FILES_PER_AGENT]
        extra = len(row.files) - len(shown)
        text = "; ".join(shown) + (f"; +{extra} more" if extra else "")
        parts.append(f"files: {text}")
    if row.reports:
        parts.append(f"{row.reports} report(s)")
    if row.approvals:
        parts.append(
            f"{len(row.approvals)} approval(s) waiting ({_count_label(row.approvals)})"
        )
    spend = f"{row.usage.credits} credits, {_thousands(row.usage.tokens)} tokens"
    if row.usage.tokens_partial:
        spend += " (tokens partial)"
    parts.append(spend)
    line = f"- {row.name} [#{row.workflow_id}]: " + " | ".join(parts)
    return prompt_budget.clip(line, MAX_AGENT_LINE_CHARS)


def compose(
    *,
    range_name: str,
    since: datetime,
    agents: list[AgentRow],
    failures: list[Failure],
    admin_only_left_out: int,
    outcomes_partial: bool,
    only: str | None = None,
) -> str:
    """The answer, cut to ``MAX_TOKENS``. What is cut is counted and said."""
    active = [a for a in agents if a.active]
    quiet = len(agents) - len(active)
    label = {"today": "today", "7d": "last 7 days", "30d": "last 30 days"}[range_name]
    header = (
        f"Team activity, {label} (since {since.astimezone(IST):%d %b %H:%M} IST): "
        f"{len(active)} of {len(agents)} agents active."
    )
    notes: list[str] = []
    if admin_only_left_out:
        notes.append(f"{admin_only_left_out} admin-only agent(s) are not covered.")
    if outcomes_partial:
        notes.append("Outcome counts come from the newest runs only.")
    if only:
        notes.append(f"Showing {only} only.")

    # Busiest first, so a cut drops the quietest agent and not the loudest.
    ordered = sorted(
        active,
        key=lambda a: (
            -(a.failures + len(a.approvals)),
            -a.runs,
            -a.usage.credits,
            a.name,
        ),
    )
    lines = [header, *notes]
    body = [agent_line(a) for a in ordered]
    tail: list[str] = []
    if quiet:
        names = ", ".join(a.name for a in agents if not a.active)
        tail.append(prompt_budget.clip(f"Quiet: {names}.", 200))
    failure_lines: list[str] = []
    if failures:
        newest = sorted(failures, key=lambda f: f.at, reverse=True)[:MAX_FAILURES]
        failure_lines.append(
            f"Failures ({len(failures)}"
            + (f", newest {len(newest)}" if len(failures) > len(newest) else "")
            + "):"
        )
        for f in newest:
            failure_lines.append(
                f"- {f.name}, {f.at.astimezone(IST):%d %b %H:%M}: {f.reason}"
            )
    else:
        failure_lines.append("Failures: none recorded.")

    def render(agent_lines: list[str], dropped: int) -> str:
        cut = (
            [f"({dropped} more agent(s) not shown to stay short; name one to see it.)"]
            if dropped
            else []
        )
        return "\n".join([*lines, *agent_lines, *cut, *failure_lines, *tail])

    shown = list(body)
    text = render(shown, 0)
    while prompt_budget.estimate(text) > MAX_TOKENS and shown:
        shown.pop()
        text = render(shown, len(body) - len(shown))
    # Agents all gone and still long (a very long failure list): shorten that.
    if prompt_budget.estimate(text) > MAX_TOKENS:
        limit = MAX_TOKENS * 3
        text = text[:limit].rsplit("\n", 1)[0] + "\n(Cut to stay short.)"
    return text


async def report(
    session: AsyncSession,
    *,
    organization_id: int,
    arguments: Mapping[str, Any] | None,
    caller_workflow_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The answer for one workspace. ``organization_id`` is the run's own."""
    args = arguments if isinstance(arguments, Mapping) else {}
    range_name = str(args.get("range") or DEFAULT_RANGE).strip().lower()
    if range_name not in RANGES:
        return {"error": f"range must be one of {', '.join(RANGES)}."}
    now = now or datetime.now(UTC)
    since = window_start(range_name, now)

    agents, admin_only = await _agents(
        session, organization_id=organization_id, exclude_id=caller_workflow_id
    )
    wanted = " ".join(str(args.get("agent") or "").split()).lower()
    if wanted:
        matched = [a for a in agents if wanted in a.name.lower()]
        if not matched:
            names = ", ".join(a.name for a in agents[:20]) or "none"
            return {
                "report": prompt_budget.clip(
                    f"No agent here is called '{wanted}'. Agents: {names}.", 400
                )
            }
        agents = matched
    if not agents:
        return {"report": "There are no other agents in this workspace to report on."}

    rows = {a.workflow_id: a for a in agents}
    outcomes_partial = await _runs(
        session, organization_id=organization_id, rows=rows, since=since
    )
    usage = await usage_by_agent(
        session, organization_id=organization_id, workflow_ids=list(rows), since=since
    )
    for workflow_id, used in usage.items():
        rows[workflow_id].usage = used
    failures = await _events(
        session, organization_id=organization_id, rows=rows, since=since
    )
    await _approvals(session, organization_id=organization_id, rows=rows)

    return {
        "report": compose(
            range_name=range_name,
            since=since,
            agents=agents,
            failures=failures,
            admin_only_left_out=admin_only,
            outcomes_partial=outcomes_partial,
            only=agents[0].name if wanted and len(agents) == 1 else None,
        )
    }


async def run(
    organization_id: int | None,
    arguments: Mapping[str, Any] | None,
    *,
    caller_workflow_id: int | None,
) -> dict[str, Any]:
    """The tool call. Never raises: the turn must finish."""
    if not organization_id:
        return {"error": "The team's activity could not be read just now."}
    try:
        async with db_client.async_session() as session:
            return await report(
                session,
                organization_id=organization_id,
                arguments=arguments,
                caller_workflow_id=caller_workflow_id,
            )
    except Exception as exc:  # noqa: BLE001 - told to the model, not raised
        logger.warning("team_activity failed: {}", exc)
        return {"error": "The team's activity could not be read just now."}


__all__ = [
    "CONFIG_KEY",
    "DESCRIPTION",
    "FEATURE",
    "MAX_TOKENS",
    "TOOL_NAME",
    "Usage",
    "offered",
    "report",
    "run",
    "tool_properties",
    "tool_schema",
    "usage_by_agent",
    "wants_team",
]
