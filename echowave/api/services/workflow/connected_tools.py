"""The workspace's connected apps, as tools Decibyl can reach.

A bot gets its tools from the engine: every Composio tool the organisation
has connected is registered on the pipeline and the model calls it. Decibyl
had none of that. It could say what the bots did and propose a card, but it
could not look a lead up in the CRM, read today's calendar or send the
WhatsApp a person asked it to send -- the manager of an office that could
not open a drawer.

This module is the drawer. It reads the same ``tools`` rows the engine
reads, turns them into the schema shape the workspace assistant's model
client takes, and runs one on request through the same Composio client the
engine uses, billed at the same rate. Nothing here is new capability; it is
the bot's capability, reachable from the thread.

**Reads run, writes wait for a card.** The office rule is that nothing
reaches a customer or changes a system on a model's say-so. A read -- fetch,
list, search, find -- changes nothing, so Decibyl runs it in the turn and
answers from the result. Anything else is a write and goes through
``actions.propose`` as a ``run_tool`` card: a person confirms, the undo
window passes, then it runs. The split is decided by the verb in the
Composio tool slug (``GMAIL_FETCH_EMAILS`` reads, ``GMAIL_SEND_EMAIL``
writes), and an unknown verb is a write. That direction is deliberate: the
worst case of guessing "write" is a needless confirm, which somebody
notices; the worst case of guessing "read" is an email that went out.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import ToolCategory, ToolStatus
from api.services.integrations.composio.client import (
    ComposioNotConfigured,
)
from api.services.integrations.composio.client import (
    execute_tool as execute_composio_tool,
)
from api.services.workflow.tools.custom_tool import tool_to_function_schema

#: Function names the model sees, so a connected app can never shadow one of
#: Decibyl's own tools (``propose_action`` and friends).
PREFIX = "app_"

#: The verb in a Composio slug that marks a tool as changing nothing. Slugs
#: are ``TOOLKIT_VERB_OBJECT``; the verb is the second token.
READ_VERBS = frozenset(
    {
        "GET",
        "FETCH",
        "LIST",
        "SEARCH",
        "FIND",
        "READ",
        "LOOKUP",
        "RETRIEVE",
        "QUERY",
        "CHECK",
        "VIEW",
        "DESCRIBE",
        "COUNT",
        "SHOW",
    }
)

#: How far a tool result travels into the model's context. A CRM search can
#: return a lot; the model needs enough to answer, not the whole export.
MAX_RESULT_CHARS = 6_000

#: How long a tool may take from the thread. Longer than the call-time
#: default, because nobody is listening to silence here -- but bounded, so a
#: stuck SaaS cannot hold a reply open.
TIMEOUT_SECS = 20.0


def function_name(tool: Any) -> str:
    """The name the model calls this tool by: sanitised, and prefixed."""
    base = tool_to_function_schema(tool)["function"]["name"]
    return f"{PREFIX}{base}"[:64]


def toolkit_of(tool: Any) -> str | None:
    config = ((tool.definition or {}).get("config") or {}) if tool else {}
    toolkit = config.get("toolkit")
    return (
        toolkit.strip().lower()
        if isinstance(toolkit, str) and toolkit.strip()
        else None
    )


def slug_of(tool: Any) -> str | None:
    config = ((tool.definition or {}).get("config") or {}) if tool else {}
    slug = config.get("tool_slug")
    return slug.strip() if isinstance(slug, str) and slug.strip() else None


def is_read(tool: Any) -> bool:
    """Whether running this tool changes nothing. Unknown means it does."""
    slug = slug_of(tool) or ""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", slug.upper()) if p]
    if len(parts) < 2:
        return False
    return parts[1] in READ_VERBS


def is_connected(tool: Any) -> bool:
    """A Composio tool with a slug is one Decibyl can run. Anything else --
    an HTTP tool, an MCP catalogue, the call-control tools -- is not, yet."""
    return (
        getattr(tool, "category", None) == ToolCategory.COMPOSIO.value
        and slug_of(tool) is not None
    )


async def list_for_organization(organization_id: int) -> list[Any]:
    """The organisation's active connected tools. Never raises: a workspace
    whose tool list cannot be read is a workspace with no tools this turn,
    and Decibyl still answers."""
    try:
        rows = await db_client.get_tools_for_organization(
            organization_id, status=ToolStatus.ACTIVE.value
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not read connected tools for org {}: {}", organization_id, exc
        )
        return []
    return [t for t in rows if is_connected(t)]


def schema_for(tool: Any) -> dict[str, Any]:
    """One tool in the shape the workspace assistant's model client takes."""
    generated = tool_to_function_schema(tool)["function"]
    verb = "reads" if is_read(tool) else "proposes a card for"
    app = toolkit_of(tool)
    where = f" in {app}" if app else ""
    return {
        "name": function_name(tool),
        "description": (
            f"{generated['description']} ({verb} the connected app{where}.)"
        )[:1_000],
        "parameters": generated["parameters"],
    }


def schemas(tools: list[Any]) -> list[dict[str, Any]]:
    return [schema_for(t) for t in tools]


def by_function_name(tools: list[Any]) -> dict[str, Any]:
    return {function_name(t): t for t in tools}


def apps_block(tools: list[Any]) -> str:
    """The context line: which apps are connected, so the model knows what
    it can reach before it tries."""
    apps = sorted({toolkit_of(t) or "connector" for t in tools})
    if not apps:
        return "No apps connected. Connect one from Marketplace → Tools."
    reads = sum(1 for t in tools if is_read(t))
    return (
        f"Connected: {', '.join(apps)}. {len(tools)} tools ({reads} read-only). "
        "Read tools run as you answer; anything that sends, creates or changes "
        "something proposes a card first."
    )


def _bounded(result: Any) -> Any:
    """Keep a tool result inside the context budget."""
    if isinstance(result, dict) and isinstance(result.get("data"), (dict, list)):
        text = str(result["data"])
        if len(text) > MAX_RESULT_CHARS:
            return {
                **result,
                "data": text[:MAX_RESULT_CHARS] + "… (truncated)",
                "truncated": True,
            }
    return result


async def execute(
    *,
    organization_id: int,
    tool: Any,
    arguments: dict[str, Any],
    ref_id: str,
) -> dict[str, Any]:
    """Run one connected tool for this organisation and bill it.

    Same ``{"status": ...}`` envelope the engine hands its model, so the
    assistant sees one contract. Charged only on success, keyed on
    ``ref_id`` so a retried turn never charges twice. Never raises.
    """
    slug = slug_of(tool)
    if not slug:
        return {"status": "error", "error": f"{tool.name} is misconfigured"}
    config = (tool.definition or {}).get("config") or {}
    try:
        result = await execute_composio_tool(
            tool_slug=slug,
            arguments=arguments or {},
            organization_id=organization_id,
            connected_account_id=config.get("connected_account_id"),
            timeout_secs=TIMEOUT_SECS,
        )
    except ComposioNotConfigured as exc:
        logger.error("Connected tool {} unavailable: {}", slug, exc)
        return {"status": "error", "error": f"{tool.name} is not available right now"}
    except Exception as exc:  # noqa: BLE001 - the thread must keep answering
        logger.error("Connected tool {} failed: {}", slug, exc)
        return {"status": "error", "error": str(exc)}

    if result.get("status") == "success":
        # One credit a call, three on a premium connector (KAN-56), the same
        # price a bot pays for the same call.
        from api.services.billing import events as billing_events

        await billing_events.charge_in_own_session(
            organization_id=organization_id,
            event=billing_events.tool_call_event(toolkit_of(tool)),
            ref_id=ref_id,
            note=f"{tool.name} via {toolkit_of(tool) or 'connector'} (Decibyl)",
        )
    return _bounded(result)


__all__ = [
    "MAX_RESULT_CHARS",
    "PREFIX",
    "READ_VERBS",
    "apps_block",
    "by_function_name",
    "execute",
    "function_name",
    "is_connected",
    "is_read",
    "list_for_organization",
    "schema_for",
    "schemas",
    "slug_of",
    "toolkit_of",
]
