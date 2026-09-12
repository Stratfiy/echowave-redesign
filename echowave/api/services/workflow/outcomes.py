"""What happens once the call is over.

A call is not finished when it is answered. It is finished when the record
exists -- the booking is in the clinic's sheet, the CRM row is raised, the
payment link has gone. That last step was the one thing the product could not
do on its own: an agent could be *given* a tool and might call it, or might
not, and "always log the booking" cannot be built out of a model's judgement.

**After the call, deliberately.** Every action here is already possible
mid-conversation, and for most of what a business wants that is the wrong
shape. Writing a row costs a second or more of a live line -- measured at
600ms for a trivial call and 1,185ms for Gmail -- and a caller listening to
silence while we talk to Google is a worse experience than one whose booking is
filed thirty seconds after they hang up. Nothing here is on the caller's clock,
so nothing here needs a filler phrase, a timeout budget, or a decision about
whether it is worth the wait.

Each action is recorded as an app interaction against the run, which is what
makes the outcome rate mean anything: the metric and the promise are then the
same event, rather than a number computed near one.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from loguru import logger

from api.db import db_client
from api.enums import ToolCategory
from api.schemas.workflow_configurations import OutcomeAction
from api.services.integrations.composio.client import (
    execute_tool as execute_composio_tool,
)
from api.services.integrations.google_calendar.client import (
    execute_google_calendar_tool,
)
from api.services.workflow import app_interactions
from api.services.workflow.tools.custom_tool import execute_http_tool
from api.utils.template_renderer import render_template

#: Tool kinds an outcome may run. Everything else on the tool list is a thing
#: that only makes sense mid-conversation -- ending a call that has ended,
#: transferring a caller who has hung up, a calculator answering nobody -- and
#: offering them here would be offering a button that cannot work.
RUNNABLE_KINDS: frozenset[str] = frozenset(
    {
        ToolCategory.HTTP_API.value,
        ToolCategory.COMPOSIO.value,
        ToolCategory.GOOGLE_CALENDAR.value,
    }
)


def parse_actions(configurations: Mapping[str, Any] | None) -> list[OutcomeAction]:
    """The configured actions, skipping any that will not validate.

    One malformed action must not stop the others. This runs after the call,
    where the alternative to a partial result is no result -- and an operator
    whose CRM step is broken still wants their sheet written.
    """
    raw = (configurations or {}).get("outcome_actions")
    if not isinstance(raw, list):
        return []

    actions: list[OutcomeAction] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            actions.append(OutcomeAction.model_validate(entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping a malformed outcome action: {}", exc)
    return actions


def actions_for(
    actions: list[OutcomeAction], disposition: Optional[str]
) -> list[OutcomeAction]:
    """Which of them this call's outcome fires.

    An empty ``when`` fires on every call. Matching is case-insensitive because
    a disposition code is typed by an operator in one screen and produced by a
    classifier in another, and "Booked" failing to match "booked" is a silent
    no-op with no symptom -- exactly the class of bug this codebase keeps
    making (see api/AGENTS.md).
    """
    code = (disposition or "").strip().lower()
    fired: list[OutcomeAction] = []
    for action in actions:
        if not action.enabled:
            continue
        if not action.when:
            fired.append(action)
            continue
        if code and code in {w.strip().lower() for w in action.when}:
            fired.append(action)
    return fired


def render_arguments(
    arguments: Mapping[str, str], render_context: Mapping[str, Any]
) -> dict[str, Any]:
    """Fill an action's argument templates from the call's own context.

    A template that renders to nothing is passed as an empty string rather than
    dropped. The provider rejecting a blank required field is a visible error
    in the interactions table; a silently absent key is a request that means
    something different from the one the operator wrote.
    """
    rendered: dict[str, Any] = {}
    for name, template in (arguments or {}).items():
        try:
            value = render_template(template, dict(render_context))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not render outcome argument {}: {}", name, exc)
            value = ""
        rendered[name] = "" if value is None else value
    return rendered


async def _execute(
    tool: Any, arguments: dict[str, Any], organization_id: int, render_context: Mapping
) -> dict[str, Any]:
    """Run one tool, in the same shape its in-call handler would."""
    if tool.category == ToolCategory.COMPOSIO.value:
        config = (tool.definition or {}).get("config") or {}
        return await execute_composio_tool(
            tool_slug=config.get("tool_slug") or "",
            arguments=arguments,
            organization_id=organization_id,
        )
    if tool.category == ToolCategory.GOOGLE_CALENDAR.value:
        return await execute_google_calendar_tool(
            tool=tool, arguments=arguments, organization_id=organization_id
        )
    return await execute_http_tool(
        tool,
        arguments,
        call_context_vars=render_context.get("initial_context"),
        gathered_context_vars=render_context.get("gathered_context"),
        organization_id=organization_id,
    )


async def run_for_call(
    *,
    organization_id: Optional[int],
    workflow_run_id: int,
    workflow_id: Optional[int],
    definition_id: Optional[int],
    configurations: Mapping[str, Any] | None,
    disposition: Optional[str],
    render_context: Mapping[str, Any],
) -> int:
    """Run every action this call's outcome fires. Returns how many succeeded.

    Never raises. It shares the post-call pipeline with the customer's
    confirmation message, and an outcome step that throws must not take that
    with it.
    """
    if not organization_id:
        return 0

    fired = actions_for(parse_actions(configurations), disposition)
    if not fired:
        return 0

    tools = await db_client.get_tools_by_uuids(
        [a.tool_uuid for a in fired], organization_id
    )
    by_uuid = {tool.tool_uuid: tool for tool in tools}

    succeeded = 0
    for action in fired:
        tool = by_uuid.get(action.tool_uuid)
        if tool is None:
            # Deleted, or belonging to another organization -- get_tools_by_uuids
            # is org-scoped, so this is also the tenant check.
            logger.warning(
                "Outcome action references tool {} which this account does not "
                "have; skipping",
                action.tool_uuid,
            )
            continue
        if tool.category not in RUNNABLE_KINDS:
            logger.warning(
                "Outcome action cannot run a {} tool after a call; skipping",
                tool.category,
            )
            continue

        arguments = render_arguments(action.arguments, render_context)
        try:
            result = await _execute(tool, arguments, organization_id, render_context)
            status, error = app_interactions.classify_result(result)
        except Exception as exc:  # noqa: BLE001 - one broken step, not all of them
            logger.warning("Outcome action {} failed: {}", tool.name, exc)
            status, error = app_interactions.STATUS_ERROR, str(exc)[:1000]

        if status == app_interactions.STATUS_SUCCESS:
            succeeded += 1

        # Recorded against the run, which is what makes outcome rate mean what
        # it says: the promise and the metric become the same event.
        await app_interactions.record(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            workflow_id=workflow_id,
            definition_id=definition_id,
            kind=tool.category,
            app=_app_slug(tool),
            name=f"outcome:{tool.name}",
            status=status,
            error=error,
            duration_ms=None,
        )

    logger.info(
        "Ran {} outcome action(s) for run {}, {} succeeded",
        len(fired),
        workflow_run_id,
        succeeded,
    )
    return succeeded


def _app_slug(tool: Any) -> Optional[str]:
    """Same derivation the in-call recorder uses, so one connector reads as one
    row in the reliability report whether it was called during or after."""
    from api.services.workflow.pipecat_engine_custom_tools import _tool_app_slug

    return _tool_app_slug(tool)
