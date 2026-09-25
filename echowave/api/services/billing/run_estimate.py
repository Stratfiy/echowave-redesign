"""What a run of this agent is likely to cost, and what it may not exceed (OP-5).

The Prospecting design: the user sets the spend cap, and the estimate and
the hard maximum are always shown. Three numbers, in credits, all derived
from what the agent actually has rather than typed in:

- **Estimate per run**, from the agent's tools and the exchange table: a
  search is a tool call plus the vendor's price at cost; a page read is a
  tool call; a connected-app call is a tool call at the connector's rate;
  a text turn is a reply; a routine firing is a routine run. Assumed
  counts are shown beside every line, because an estimate whose
  assumptions are hidden is a number nobody can argue with.
- **Hard maximum per run**, from the caps the platform enforces: the page
  cap on the fetcher, the script's external-spend cap, the turn count a
  run is allowed. What a run cannot exceed whatever the model does.
- **The cap**, the agent's own budget policy (S-1), the one thing here
  the operator sets. None set is said, not zeroed.

An estimate is an estimate: the replay replaces these assumptions with
measured ones, and until then every figure carries ``provisional``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api import constants
from api.enums import CostComponent, ToolCategory
from api.services.billing import events
from api.services.billing.credits import PAISE_PER_CREDIT

#: Assumed counts per item handled, until the replay measures them.
SEARCHES_PER_ITEM = 1
PAGES_PER_ITEM = 2
TURNS_PER_ITEM = 2
WRITES_PER_ITEM = 1
DEFAULT_ITEMS_PER_RUN = 10
#: Turns a text run is allowed before it stops; the ceiling on replies.
MAX_TURNS_PER_RUN = 40
#: Routine firings a month for each cadence the routine model knows.
RUNS_PER_MONTH = {"hourly": 30 * 24, "daily": 30, "weekdays": 22, "weekly": 4}


@dataclass(frozen=True)
class Line:
    what: str
    count: int
    credits_each: float
    provisional: bool = False

    @property
    def credits(self) -> float:
        return round(self.count * self.credits_each, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "what": self.what,
            "count": self.count,
            "credits_each": self.credits_each,
            "credits": self.credits,
            "provisional": self.provisional,
        }


@dataclass(frozen=True)
class Shape:
    """What the agent has, read off its definition and tools."""

    has_web: bool = False
    pages_cap: int = constants.WEB_FETCH_MAX_PAGES_PER_RUN
    connected_writes: int = 0
    premium_writes: int = 0
    can_run_scripts: bool = False
    script_cap_credits: int = constants.SCRIPT_EXTERNAL_SPEND_CAP_CREDITS
    runs_per_month: int = 0
    is_routine: bool = False


@dataclass(frozen=True)
class Estimate:
    items_per_run: int
    lines: list[Line]
    hard_maximum: list[Line]
    runs_per_month: int
    search_pass_through_credits: float
    notes: list[str] = field(default_factory=list)

    @property
    def per_run_credits(self) -> float:
        return round(sum(line.credits for line in self.lines), 2)

    @property
    def hard_maximum_credits(self) -> float:
        return round(sum(line.credits for line in self.hard_maximum), 2)

    @property
    def per_month_credits(self) -> float:
        return round(self.per_run_credits * self.runs_per_month, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "items_per_run": self.items_per_run,
            "per_run_credits": self.per_run_credits,
            "per_month_credits": self.per_month_credits,
            "runs_per_month": self.runs_per_month,
            "hard_maximum_per_run_credits": self.hard_maximum_credits,
            "lines": [line.as_dict() for line in self.lines],
            "hard_maximum": [line.as_dict() for line in self.hard_maximum],
            "notes": list(self.notes),
        }


def _credits(event: str) -> float:
    return events.credits_for(event)


def shape_of(
    definition: dict[str, Any] | None,
    tools: list[Any],
    *,
    configurations: dict[str, Any] | None,
    routines: list[Any],
    can_run_scripts: bool,
) -> Shape:
    """The agent's shape from its rows. Pure."""
    from api.services.sandbox import code_mode
    from api.services.workflow import agent_web, connected_tools, unattended

    has_web = False
    pages_cap = constants.WEB_FETCH_MAX_PAGES_PER_RUN
    writes = premium = 0
    for tool in tools:
        if agent_web.is_web_tool(tool):
            has_web = agent_web.enabled()
            own = agent_web.limits_of(tool)["max_pages"]
            if own:
                pages_cap = min(pages_cap, int(own))
            continue
        if getattr(tool, "category", None) != ToolCategory.COMPOSIO.value:
            continue
        if connected_tools.is_read(tool) or unattended.is_staged(tool):
            continue
        writes += 1
        if (
            events.tool_call_event(
                connected_tools.toolkit_of(tool),
                connected_tools.slug_of(tool) or getattr(tool, "name", None),
            )
            == events.TOOL_CALL_PREMIUM
        ):
            premium += 1
    runs = 0
    for routine in routines:
        if not getattr(routine, "is_active", True):
            continue
        runs += RUNS_PER_MONTH.get(str(getattr(routine, "cadence", "") or ""), 30)
    return Shape(
        has_web=has_web,
        pages_cap=pages_cap,
        connected_writes=writes,
        premium_writes=premium,
        can_run_scripts=can_run_scripts,
        script_cap_credits=code_mode.cap_paise_for(configurations) // PAISE_PER_CREDIT,
        runs_per_month=runs,
        is_routine=bool(routines),
    )


def estimate(
    shape: Shape,
    *,
    items_per_run: int = DEFAULT_ITEMS_PER_RUN,
    search_pass_through_paise: int | None = None,
) -> Estimate:
    """The three numbers. Pure: everything it needs is in ``shape``."""
    items = max(1, int(items_per_run))
    notes: list[str] = []
    pass_through = (search_pass_through_paise or 0) / PAISE_PER_CREDIT
    if shape.has_web and search_pass_through_paise is None:
        notes.append(
            "No rate on file for the search vendor; searches are shown at the "
            "tool-call fee alone."
        )
    lines: list[Line] = []
    if shape.is_routine:
        lines.append(Line("Routine run", 1, _credits(events.ROUTINE_RUN)))
    lines.append(
        Line("Text turns", items * TURNS_PER_ITEM, _credits(events.TEXT_REPLY))
    )
    if shape.has_web:
        lines.append(
            Line(
                "Web searches (fee plus the vendor's price)",
                items * SEARCHES_PER_ITEM,
                round(_credits(events.TOOL_CALL) + pass_through, 3),
                provisional=True,
            )
        )
        lines.append(
            Line(
                "Page reads",
                min(items * PAGES_PER_ITEM, shape.pages_cap),
                _credits(events.TOOL_CALL),
            )
        )
    if shape.connected_writes:
        premium_share = shape.premium_writes / shape.connected_writes
        each = round(
            _credits(events.TOOL_CALL_PREMIUM) * premium_share
            + _credits(events.TOOL_CALL) * (1 - premium_share),
            2,
        )
        lines.append(
            Line("Connected-app calls (a send each)", items * WRITES_PER_ITEM, each)
        )

    hard: list[Line] = []
    if shape.is_routine:
        hard.append(Line("Routine run", 1, _credits(events.ROUTINE_RUN)))
    hard.append(
        Line(
            "Text turns, at the run's turn cap",
            MAX_TURNS_PER_RUN,
            _credits(events.TEXT_REPLY),
        )
    )
    if shape.has_web:
        hard.append(
            Line(
                "Web searches, one per page allowed",
                shape.pages_cap,
                round(_credits(events.TOOL_CALL) + pass_through, 3),
                provisional=True,
            )
        )
        hard.append(
            Line(
                "Page reads, at the page cap",
                shape.pages_cap,
                _credits(events.TOOL_CALL),
            )
        )
        if shape.can_run_scripts:
            hard.append(
                Line(
                    "Inside a script, at its spend cap",
                    1,
                    float(shape.script_cap_credits),
                )
            )
    if shape.can_run_scripts:
        hard.append(Line("Script run", 1, _credits(events.SCRIPT_RUN)))
    if shape.connected_writes:
        hard.append(
            Line(
                "Connected-app calls, one per turn",
                MAX_TURNS_PER_RUN,
                _credits(
                    events.TOOL_CALL_PREMIUM
                    if shape.premium_writes
                    else events.TOOL_CALL
                ),
            )
        )
    notes.append(
        f"Assumes {items} item(s) a run, {SEARCHES_PER_ITEM} search, "
        f"{PAGES_PER_ITEM} page reads, {TURNS_PER_ITEM} turns and "
        f"{WRITES_PER_ITEM} send each. Measured figures replace these after the replay."
    )
    from api.services.billing import exchange

    if exchange.enabled():
        # The figures above are the exchange table's (events.credits_for);
        # this says what they leave out.
        notes.append(
            f"Priced on the {exchange.VERSION} rate card. Each turn includes "
            f"{exchange.STANDARD_TOKENS_PER_EVENT:,} tokens on a standard model; "
            "a premium model adds its tokens on top."
        )
    return Estimate(
        items_per_run=items,
        lines=lines,
        hard_maximum=hard,
        runs_per_month=shape.runs_per_month,
        search_pass_through_credits=round(pass_through, 3),
        notes=notes,
    )


async def _search_pass_through_paise(session) -> int | None:
    from api.services.billing.money import cost_paise
    from api.services.billing.rates import resolve_provider_rate
    from api.services.workflow import web_tools

    try:
        rate = await resolve_provider_rate(
            session,
            provider=web_tools.SEARCH_PROVIDER,
            component=CostComponent.DATA,
            at=datetime.now(UTC),
            model=web_tools.SEARCH_KIND,
        )
    except Exception as exc:  # noqa: BLE001 - shown without, and said
        logger.warning("Could not read the search rate: {}", exc)
        return None
    if rate is None:
        return None
    return cost_paise(quantity=1, rate_mpaise=rate.rate_mpaise, unit=rate.unit)


async def for_workflow(
    session,
    *,
    organization_id: int,
    workflow_id: int,
    items_per_run: int = DEFAULT_ITEMS_PER_RUN,
) -> dict[str, Any] | None:
    """The estimate, the hard maximum and the cap for one agent, or None
    when the agent is not this workspace's."""
    from api.db import db_client
    from api.services.billing import budgets
    from api.services.sandbox import code_mode
    from api.services.workflow import readiness

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        return None
    definition = dict(getattr(workflow, "workflow_definition", None) or {})
    configurations = getattr(workflow, "workflow_configurations", None)
    uuids = readiness.required_tool_uuids(definition, configurations)
    tools = await db_client.get_tools_by_uuids(uuids, organization_id) if uuids else []
    routines = list(
        await db_client.routines_for_workflow(
            workflow_id, organization_id=organization_id
        )
    )
    shape = shape_of(
        definition,
        tools,
        configurations=configurations if isinstance(configurations, dict) else None,
        routines=routines,
        can_run_scripts=await code_mode.allowed(organization_id),
    )
    est = estimate(
        shape,
        items_per_run=items_per_run,
        search_pass_through_paise=await _search_pass_through_paise(session)
        if shape.has_web
        else 0,
    )
    cap: dict[str, Any] | None = None
    if budgets.enabled():
        for policy in await budgets.policies_for(
            session, organization_id=organization_id, workflow_id=workflow_id
        ):
            if policy.workflow_id != workflow_id:
                continue
            start, end = budgets.window_bounds(
                policy.window_kind, at=datetime.now(UTC), opened_at=policy.created_at
            )
            observed = await budgets.spend_paise(
                session,
                organization_id=organization_id,
                workflow_id=workflow_id,
                start=start,
                end=end,
            )
            cap = {
                "policy_id": policy.id,
                "window_kind": policy.window_kind,
                "credits": int(policy.amount_paise) // PAISE_PER_CREDIT,
                "spent_credits": observed // PAISE_PER_CREDIT,
                "hard_stop": bool(policy.hard_stop),
            }
            break
    return {
        "workflow_id": workflow_id,
        "estimate": est.as_dict(),
        "caps": {
            "pages_per_run": shape.pages_cap,
            "script_external_credits_per_run": shape.script_cap_credits
            if shape.can_run_scripts
            else None,
            "agent_budget": cap,
            "budgets_enabled": budgets.enabled(),
        },
    }


__all__ = ["Estimate", "Line", "Shape", "estimate", "for_workflow", "shape_of"]
