"""Code Mode: a routine that touches many rows runs as one script (Step 20).

A bot chasing two hundred receivables used to do it turn by turn: read a
page of the sheet, decide, send, read the next page, and every row cost a
model turn and a tool-call credit. Now the bot writes one short script,
the script runs in a box with the bot's own tools reachable by name, and
the run is one thing on the receipt: a script run, 4 credits, the calls
inside it not counted.

Three rules the tool keeps:

- **Everyday and above.** A Free account is not offered the tool at all;
  a model that cannot see it cannot ask for it.
- **Only the bot's own tools.** A script may call what the node it runs
  on may call, by the same names the model sees, and nothing else. There
  is no way to name a tool the bot was not given.
- **One charge, at the end, when the box ran.** A box that never started
  costs nothing. A script that ran and failed cost the run.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from api.constants import SANDBOX_URL
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.sandbox import jobs, protocol, spill
from api.services.sandbox.runner import Runner

TOOL_NAME = "run_script"
DESCRIPTION = (
    "Run a short Python script in a sandbox to do a job that touches many "
    "rows or many records at once, instead of doing it one call at a time. "
    "Inside the script, the apps you have as tools are reachable by the "
    "same names: tools.call('tool_name', arg=value) returns the tool's data, "
    "and tools.spilled(stored_as='…') reads a response that was too large to "
    "show you. print() what you want to report; the printed output comes "
    "back to you. Use it when one call returned a preview of many rows, or "
    "when a routine would otherwise need more than about ten tool calls. "
    f"Limits: {protocol.DEFAULT_MAX_CALLS} tool calls and "
    f"{protocol.DEFAULT_TIMEOUT_SECONDS // 60} minutes; no network; nothing "
    "outside the tools you already have."
)

#: Plans that may run scripts. Free is not one of them.
ALLOWED_PLANS = frozenset({"everyday", "business", "growth", "scale"})


def tool_properties() -> dict[str, Any]:
    return {
        "code": {
            "type": "string",
            "description": (
                "The Python script. Plain Python 3; tools.call(...) for the "
                "apps; print() for what you want back. Keep it under a few "
                "dozen lines."
            ),
        },
        "why": {
            "type": "string",
            "description": "One line on what the script is for, for the timeline.",
        },
    }


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": tool_properties(),
            "required": ["code", "why"],
        },
    }


async def allowed(organization_id: Optional[int]) -> bool:
    """Whether this account is offered the tool: a paid plan, and a box to
    run in. Never raises; a plan that cannot be read is Free."""
    if not organization_id:
        return False
    if not SANDBOX_URL and not _local_allowed():
        return False
    try:
        from api.services.billing.subscription_plans import plan_for_organization

        async with db_client.async_session() as session:
            plan = await plan_for_organization(session, organization_id=organization_id)
        return str(plan.code) in ALLOWED_PLANS
    except Exception as exc:  # noqa: BLE001 - not offered is the safe answer
        logger.warning("Could not read the plan for org {}: {}", organization_id, exc)
        return False


def _local_allowed() -> bool:
    from api.services.sandbox.runner import LocalRunner, SandboxUnavailable

    try:
        LocalRunner()
    except SandboxUnavailable:
        return False
    return True


def _names(tool: Any) -> set[str]:
    """Every name the model may know a tool by: the engine's, and the
    thread's prefixed one, so a script written on either surface works."""
    from api.services.workflow import connected_tools
    from api.services.workflow.tools.custom_tool import tool_to_function_schema

    plain = tool_to_function_schema(tool)["function"]["name"]
    return {plain, connected_tools.function_name(tool)}


async def run_for_bot(
    *,
    organization_id: int,
    code: str,
    why: str,
    tools: list[Any],
    workflow_id: Optional[int] = None,
    workflow_run_id: Optional[int] = None,
    ref_id: str,
    runner: Optional[Runner] = None,
) -> dict[str, Any]:
    """Run one script for one bot, with that bot's connected tools reachable
    by name, and bill one script run. Returns what the model is told."""
    from api.services.billing import events as billing_events
    from api.services.integrations.composio.client import (
        execute_tool as execute_composio_tool,
    )
    from api.services.workflow import agent_timeline, connected_tools

    if not code.strip():
        return {"status": "error", "error": "The script is empty."}
    if not await allowed(organization_id):
        return {
            "status": "unavailable",
            "reason": "Scripts run on Everyday and above; this account cannot run one.",
        }

    by_name: dict[str, Any] = {}
    for tool in tools:
        if not connected_tools.is_connected(tool):
            continue
        for name in _names(tool):
            by_name[name] = tool

    async def call(name: str, args: dict[str, Any]) -> Any:
        tool = by_name.get(name)
        if tool is None:
            return {
                "status": "error",
                "error": f"no tool called {name!r} on this bot; the tools are: "
                + ", ".join(sorted(by_name)),
            }
        config = (tool.definition or {}).get("config") or {}
        return await execute_composio_tool(
            tool_slug=str(config.get("tool_slug") or ""),
            arguments=args,
            organization_id=organization_id,
            connected_account_id=config.get("connected_account_id"),
        )

    async def spilled(stored_as: str) -> Any:
        return await spill.read_spilled(organization_id, stored_as)

    result = await jobs.run(
        organization_id=organization_id,
        code=code,
        tools=call,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        runner=runner,
        spilled_reader=spilled,
    )

    ran = result.status != jobs.FAILED or result.exit_code is not None
    if ran:
        # The one charge. Keyed on the run and the call so a retried turn
        # charges once. The calls the script made inside are not billed.
        try:
            await billing_events.charge_in_own_session(
                organization_id=organization_id,
                event=billing_events.SCRIPT_RUN,
                ref_id=ref_id,
                note=(why or "script")[:80],
            )
        except Exception as exc:  # noqa: BLE001 - the run happened; log it
            logger.error(
                "Could not charge a script run for org {}: {}", organization_id, exc
            )

    line = (
        f"Ran a script: {result.calls} tool call{'s' if result.calls != 1 else ''}, "
        f"{round((result.finished_at - result.started_at).total_seconds())}s"
        + ("" if result.ok else f", {result.status.replace('_', ' ')}")
    )
    try:
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.AGENT_ACTED.value
            if result.ok
            else AgentEventKind.COULD_NOT.value,
            actor=AgentEventActor.AGENT.value,
            summary=f"{line}. {why}"[:500],
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            payload={
                "script": {
                    "job_id": result.job_id,
                    "status": result.status,
                    "calls": result.calls,
                    "output": result.output[-2_000:],
                    "error": result.error[-1_000:],
                    "why": why[:300],
                }
            },
            in_channel=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not record a script run: {}", exc)

    return result.as_result()


__all__ = [
    "ALLOWED_PLANS",
    "DESCRIPTION",
    "TOOL_NAME",
    "allowed",
    "run_for_bot",
    "tool_properties",
    "tool_schema",
]
