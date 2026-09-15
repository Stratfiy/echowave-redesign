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
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import ToolCategory, ToolStatus
from api.services.integrations.composio import schema as tool_schema
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

#: The one tool that stands in for every connected app's argument list.
#:
#: **Deferred loading.** A workspace with thirty connected tools would put
#: thirty argument schemas into every turn, most of them for tools this
#: turn will never call. So the model is shown each tool by name and one
#: line, and asked to call this first for the one it means to use. The reply
#: is that tool's full schema, and from the next round the tool is offered
#: with it. Two hops for the first use of a tool in a thread; one line per
#: tool on every other turn.
LOAD_TOOL_NAME = "load_tool"

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


def _description(tool: Any, generated: dict[str, Any]) -> str:
    verb = "reads" if is_read(tool) else "proposes a card for"
    app = toolkit_of(tool)
    where = f" in {app}" if app else ""
    return f"{generated['description']} ({verb} the connected app{where}.)"


def schema_for(tool: Any, full: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """One tool in the shape the workspace assistant's model client takes.

    With ``full`` -- the tool's argument schema, from
    :func:`load` -- the model gets every argument. Without it, the row's own
    declared parameters, which for a tool attached from the chat is none.
    """
    generated = tool_to_function_schema(tool)["function"]
    parameters = generated["parameters"]
    if full and not parameters.get("properties"):
        parameters = full
    return {
        "name": function_name(tool),
        "description": _description(tool, generated)[:1_000],
        "parameters": parameters,
    }


def index_entry(tool: Any) -> dict[str, Any]:
    """The tool by name and one line, with no arguments: what every turn
    carries for a tool it has not loaded."""
    generated = tool_to_function_schema(tool)["function"]
    return {
        "name": function_name(tool),
        "description": (
            f"{_description(tool, generated)} Call {LOAD_TOOL_NAME} with this "
            "name first to get its arguments."
        )[:1_000],
        "parameters": {"type": "object", "properties": {}},
    }


def load_tool_schema() -> dict[str, Any]:
    return {
        "name": LOAD_TOOL_NAME,
        "description": (
            "Get the full argument list of one connected-app tool before "
            "calling it. The app_… tools are listed by name only; call this "
            "with the exact name of the one you need, then call that tool "
            "with the arguments it returns. Load only what this reply needs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The app_… tool name, exactly as listed.",
                }
            },
            "required": ["name"],
        },
    }


def schemas(
    tools: list[Any], loaded: Optional[dict[str, dict[str, Any]]] = None
) -> list[dict[str, Any]]:
    """The connected apps as the model sees them this round.

    ``loaded`` maps a function name to its argument schema, for the tools
    the thread has asked about; those are offered in full. Every other tool
    is a name and a line, plus the one tool that loads the rest. No tools,
    no loader: an empty workspace is not offered a way to load nothing.
    """
    if not tools:
        return []
    loaded = loaded or {}
    out = [load_tool_schema()]
    for tool in tools:
        name = function_name(tool)
        if name in loaded:
            out.append(schema_for(tool, loaded[name]))
        else:
            out.append(index_entry(tool))
    return out


async def load(tool: Any) -> dict[str, Any]:
    """The tool's argument schema, for the model that asked.

    A schema that could not be read is not an error the model can act on:
    it is told to call the tool with what the description implies, which is
    what it would have done before deferred loading existed.
    """
    slug = slug_of(tool) or ""
    declared = tool_to_function_schema(tool)["function"]["parameters"]
    schema = (
        declared if declared.get("properties") else await tool_schema.input_schema(slug)
    )
    if not schema:
        return {
            "status": "success",
            "tool": function_name(tool),
            "parameters": {"type": "object", "properties": {}},
            "note": (
                "No argument list is published for this tool. Call it with "
                "the arguments its description implies."
            ),
        }
    return {"status": "success", "tool": function_name(tool), "parameters": schema}


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
        f"something proposes a card first. Tools are listed by name; call "
        f"{LOAD_TOOL_NAME} for the one you need before using it."
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
    "LOAD_TOOL_NAME",
    "MAX_RESULT_CHARS",
    "PREFIX",
    "READ_VERBS",
    "apps_block",
    "by_function_name",
    "execute",
    "function_name",
    "index_entry",
    "is_connected",
    "is_read",
    "list_for_organization",
    "load",
    "load_tool_schema",
    "schema_for",
    "schemas",
    "slug_of",
    "toolkit_of",
]
