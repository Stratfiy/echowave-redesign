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

And one more since OP-5 (21 Sept 2026):

- **What the box buys is capped per run.** A connected-app call from
  inside the script is the customer's own account and stays uncounted, as
  priced. A call on the platform's metered tools -- a search on our key,
  a page read, on a bot that has the web tool -- is charged as it is
  anywhere else, and the run may spend at most
  ``SCRIPT_EXTERNAL_SPEND_CAP_CREDITS`` on them (a bot may set a lower
  figure). Past the cap the call is refused and the script hears why.
  This is the budget the Prospecting design required before a metered
  provider was reachable from a script, and it is what makes one so.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api import constants
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
    "show you. print() a short summary of what was done, not every row; "
    "only the last few thousand characters of output come back to you. Use "
    "it when one call returned a preview of many rows, or "
    "when a routine would otherwise need more than about ten tool calls. "
    f"Limits: {protocol.DEFAULT_MAX_CALLS} tool calls and "
    f"{protocol.DEFAULT_TIMEOUT_SECONDS // 60} minutes; no network; nothing "
    "outside the tools you already have. On an agent that has the web tool, "
    "tools.call('web_search', query=...) and tools.call('web_fetch', url=...) "
    "work too, each priced as usual and capped per run."
)

#: The key on a bot's configurations for a lower per-run cap, in credits.
SPEND_CAP_CONFIG_KEY = "script_spend_cap_credits"

#: The platform's metered tools a script may call, when the bot has them.
METERED_TOOLS = ("web_search", "web_fetch")


def cap_paise_for(configurations: dict[str, Any] | None = None) -> int:
    """The per-run external-spend cap for a bot: its own figure when it
    has a plain positive one, the platform's otherwise."""
    from api.services.billing.credits import PAISE_PER_CREDIT

    cap = constants.SCRIPT_EXTERNAL_SPEND_CAP_CREDITS
    if isinstance(configurations, dict):
        own = configurations.get(SPEND_CAP_CONFIG_KEY)
        if isinstance(own, int) and not isinstance(own, bool) and 0 < own < cap:
            cap = own
    return max(0, cap) * PAISE_PER_CREDIT


#: Plans that may run scripts. Free is not one of them; the trial is.
ALLOWED_PLANS = frozenset(
    {
        # PLAN-1 (KAN-255): a trial sees the product's real shape, under the
        # same per-run credit caps as every plan.
        "trial",
        "everyday",
        "business",
        "growth",
        "scale",
        # The 21 September ladder (PLAN_LADDER_2026_09_ENABLED): Go is the
        # Everyday rung's successor at the same price, so it keeps the tool.
        "go",
        "personal",
        "business_v2",
        "pro",
        "scale_v2",
        "personal_global",
        "business_global",
        "pro_global",
        "scale_global",
    }
)


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


async def allowed(organization_id: int | None) -> bool:
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
        from api.services.billing import free_mode

        return free_mode.on(organization_id) or str(plan.code) in ALLOWED_PLANS
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
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
    ref_id: str,
    runner: Runner | None = None,
    spend_cap_paise: int | None = None,
    run_key: str | None = None,
) -> dict[str, Any]:
    """Run one script for one bot, with that bot's connected tools reachable
    by name, and bill one script run. Returns what the model is told.

    ``spend_cap_paise`` bounds what the script may spend on the platform's
    metered tools (OP-5); None reads the bot's own figure or the platform's.
    ``run_key`` is the page cap's identity for a fetch from inside the box."""
    from api.services.billing import events as billing_events
    from api.services.integrations.composio.client import (
        execute_tool as execute_composio_tool,
    )
    from api.services.workflow import agent_timeline, agent_web, connected_tools

    if not code.strip():
        return {"status": "error", "error": "The script is empty."}
    if not await allowed(organization_id):
        return {
            "status": "unavailable",
            "reason": "Scripts run on Everyday and above; this account cannot run one.",
        }

    by_name: dict[str, Any] = {}
    web_row = None
    for tool in tools:
        if agent_web.is_web_tool(tool):
            web_row = tool
            continue
        if not connected_tools.is_connected(tool):
            continue
        for name in _names(tool):
            by_name[name] = tool

    web_on = web_row is not None and agent_web.enabled()
    if spend_cap_paise is None:
        configurations = None
        if workflow_id:
            try:
                workflow = await db_client.get_workflow_by_id(workflow_id)
                configurations = getattr(workflow, "workflow_configurations", None)
            except Exception as exc:  # noqa: BLE001 - the platform cap stands
                logger.warning(
                    "Could not read the script cap for {}: {}", workflow_id, exc
                )
        spend_cap_paise = cap_paise_for(configurations)
    spent = {"paise": 0, "refused": 0, "metered_calls": 0}
    limits = agent_web.limits_of(web_row) if web_on else {}
    known = sorted(by_name) + (list(METERED_TOOLS) if web_on else [])

    async def metered(name: str, args: dict[str, Any], call_no: int) -> Any:
        """A platform-metered call from inside the box, under the cap."""
        from api.services.billing.credits import PAISE_PER_CREDIT
        from api.services.workflow import web_tools

        if spent["paise"] + PAISE_PER_CREDIT > spend_cap_paise:
            spent["refused"] += 1
            return {
                "status": "refused",
                "error": (
                    f"This run's spend cap for searches and page reads is "
                    f"reached ({spend_cap_paise // PAISE_PER_CREDIT} credits); "
                    "finish with what you have."
                ),
            }
        call_ref = f"{ref_id}:{name}:{call_no}"
        if name == "web_search":
            result = await web_tools.search(
                organization_id,
                args,
                ref_id=call_ref,
                workflow_id=workflow_id,
                allowed_domains=limits.get("allowed_domains"),
            )
        else:
            result = await web_tools.fetch(
                organization_id,
                args,
                ref_id=call_ref,
                workflow_id=workflow_id,
                run_key=run_key,
                max_pages=limits.get("max_pages"),
                allowed_domains=limits.get("allowed_domains"),
            )
        if isinstance(result, dict):
            spent["paise"] += int(result.get("charged_paise") or 0)
            spent["metered_calls"] += 1
            result = {k: v for k, v in result.items() if k != "charged_paise"}
        return result

    async def call(name: str, args: dict[str, Any]) -> Any:
        if web_on and name in METERED_TOOLS:
            spent.setdefault("n", 0)
            spent["n"] += 1
            return await metered(name, args, spent["n"])
        tool = by_name.get(name)
        if tool is None:
            return {
                "status": "error",
                "error": f"no tool called {name!r} on this agent; the tools are: "
                + ", ".join(known),
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
        # charges once. The connected-app calls the script made inside are
        # not billed; the metered ones were, as they ran, under the cap.
        try:
            await billing_events.charge_in_own_session(
                organization_id=organization_id,
                event=billing_events.SCRIPT_RUN,
                ref_id=ref_id,
                note=(why or "script")[:80],
                workflow_id=workflow_id,
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
                    "external_spend_paise": spent["paise"],
                    "metered_calls": spent["metered_calls"],
                    "refused_over_cap": spent["refused"],
                }
            },
            in_channel=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not record a script run: {}", exc)

    out = result.as_result()
    if isinstance(out, dict) and (spent["metered_calls"] or spent["refused"]):
        from api.services.billing.credits import PAISE_PER_CREDIT

        out["external_spend_credits"] = round(spent["paise"] / PAISE_PER_CREDIT, 2)
        if spent["refused"]:
            out["note"] = (
                f"{spent['refused']} search or page read(s) were refused: the "
                f"run's spend cap of {spend_cap_paise // PAISE_PER_CREDIT} credits "
                "was reached."
            )
    return out


__all__ = [
    "ALLOWED_PLANS",
    "DESCRIPTION",
    "METERED_TOOLS",
    "SPEND_CAP_CONFIG_KEY",
    "TOOL_NAME",
    "allowed",
    "cap_paise_for",
    "run_for_bot",
    "tool_properties",
    "tool_schema",
]
