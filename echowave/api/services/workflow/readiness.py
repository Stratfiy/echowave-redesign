"""What an agent still needs before it can do its job.

Two things that look different to an operator and are the same question
underneath: "this agent wants Google Sheets and you have not connected it" and
"this agent wants Google Sheets and the connection stopped working last
Tuesday". Both mean the booking is not being written; only one of them is
anybody's fault.

So this is one checklist with three states rather than a setup wizard and a
separate health page. A wizard is finished once and then lies: a token revoked
three months later leaves a green tick over an agent that has silently stopped
filing anything.

Derived from the agent's own tools rather than from a declaration. Templates
carry no tools today -- ``TemplateNode`` is conversation only, and forbids
extra fields -- so there is nothing to read a requirement list from. Walking
what the agent actually holds is both available now and more honest: it
describes the agent in front of you rather than the pack it came from, which
may have been edited since.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from api.enums import ToolCategory

STATUS_READY = "ready"
STATUS_MISSING = "missing"
STATUS_FAILING = "failing"

#: Failures in the recent window before a connected app is called failing.
#: One error is a bad request or somebody's expired card; a run of them is the
#: connection. Set low because the cost of asking an operator to look is a
#: glance, and the cost of not asking is a month of unfiled bookings.
FAILURES_BEFORE_CONCERN = 3


def _tool_uuids_in_definition(definition: dict[str, Any] | None) -> list[str]:
    """Every tool any node of this graph can reach."""
    nodes = (definition or {}).get("nodes")
    if not isinstance(nodes, list):
        return []

    found: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        uuids = data.get("tool_uuids") if isinstance(data, dict) else None
        if isinstance(uuids, list):
            found.extend(u for u in uuids if isinstance(u, str) and u)
    return found


def _tool_uuids_in_outcomes(configurations: dict[str, Any] | None) -> list[str]:
    """Tools the agent uses after the call rather than during it.

    Counted the same as the rest. An agent whose after-call step cannot run is
    an agent that answers the phone and files nothing, which is the failure the
    product exists to prevent -- and it would be invisible if readiness only
    looked at the conversation.
    """
    actions = (configurations or {}).get("outcome_actions")
    if not isinstance(actions, list):
        return []
    return [
        action["tool_uuid"]
        for action in actions
        if isinstance(action, dict)
        and isinstance(action.get("tool_uuid"), str)
        and action["tool_uuid"]
        and action.get("enabled", True)
    ]


def required_tool_uuids(
    definition: dict[str, Any] | None, configurations: dict[str, Any] | None
) -> list[str]:
    """Every tool this agent needs, during a call and after one, deduplicated."""
    seen = dict.fromkeys(
        [
            *_tool_uuids_in_definition(definition),
            *_tool_uuids_in_outcomes(configurations),
        ]
    )
    return list(seen)


def connector_for(tool: Any) -> Optional[tuple[str, str]]:
    """The outside app this tool needs, as ``(app, label)``, or None.

    None for a calculator, a rate table, an end-call: they touch nothing, so
    there is nothing to connect and nothing to put on a checklist. Listing them
    as "ready" would pad the list with rows that can never be anything else.
    """
    category = getattr(tool, "category", None)
    definition = getattr(tool, "definition", None) or {}
    config = definition.get("config") or {}

    if category == ToolCategory.COMPOSIO.value:
        toolkit = config.get("toolkit")
        if isinstance(toolkit, str) and toolkit.strip():
            slug = toolkit.strip().lower()
            return slug, slug.replace("_", " ").title()
        return None

    if category == ToolCategory.GOOGLE_CALENDAR.value:
        return "googlecalendar", "Google Calendar"

    if category == ToolCategory.HTTP_API.value:
        # Their own endpoint. There is nothing for us to connect, but a
        # checklist that omits it entirely tells an operator the agent needs
        # two things when it depends on three.
        url = config.get("url")
        if isinstance(url, str) and url.strip():
            try:
                host = urlparse(url).hostname
            except ValueError:
                host = None
            if host:
                return f"http:{host.lower()}", host.lower()
        return None

    return None


def build_checklist(
    *,
    tools: Iterable[Any],
    connected_apps: set[str],
    failures_by_app: dict[str, int],
) -> list[dict[str, Any]]:
    """One row per outside app this agent depends on.

    Grouped by app rather than by tool: an agent with four Gmail tools has one
    Gmail problem, and four rows saying so is a worse screen and a worse
    instruction.
    """
    rows: dict[str, dict[str, Any]] = {}

    for tool in tools:
        requirement = connector_for(tool)
        if requirement is None:
            continue
        app, label = requirement

        row = rows.setdefault(
            app,
            {
                "app": app,
                "label": label,
                "status": STATUS_READY,
                "needed_by": [],
                "recent_failures": 0,
                # Their own endpoint needs no Connect button; ours do.
                "connectable": not app.startswith("http:"),
            },
        )
        name = getattr(tool, "name", None)
        if isinstance(name, str) and name and name not in row["needed_by"]:
            row["needed_by"].append(name)

    for app, row in rows.items():
        failures = int(failures_by_app.get(app, 0))
        row["recent_failures"] = failures

        if row["connectable"] and app.upper() not in connected_apps:
            row["status"] = STATUS_MISSING
        elif failures >= FAILURES_BEFORE_CONCERN:
            row["status"] = STATUS_FAILING
        else:
            row["status"] = STATUS_READY

    # Worst first. An operator opening this wants the thing to fix, not an
    # alphabetical inventory of what already works.
    order = {STATUS_MISSING: 0, STATUS_FAILING: 1, STATUS_READY: 2}
    return sorted(rows.values(), key=lambda r: (order[r["status"]], r["label"].lower()))
