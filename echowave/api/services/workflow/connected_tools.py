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
from api.services.sandbox import spill
from api.services.workflow import connector_offer
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

#: Decibyl's own verb for an app that is *not* connected, named here so the
#: context block can point at it. Imported by name rather than spelled
#: twice: a tool the prompt names and the schema does not is a tool the
#: model calls and never reaches.
OFFER_TOOL_NAME = connector_offer.TOOL_NAME

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

#: How many connected tools are named in the context block. Sixty names is
#: about 1,500 characters -- worth it, because the alternative is the model
#: guessing at its own drawer. An account past that is told the list is
#: partial rather than sold a complete one.
MAX_NAMED = 60

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


async def mcp_for_organization(organization_id: int) -> list[Any]:
    """The organisation's MCP tools: apps connected through their own server.

    A sibling of :func:`list_for_organization` rather than part of it,
    because the two answer different questions. That one is "what can
    Decibyl run", and an MCP row is not that -- its execute path is
    Composio's. This one is "what has the account connected", which is what
    the bot builder and the context block need, and they need it precisely
    because leaving MCP out of both is how Decibyl came to deny that a
    connected app was connected.

    Never raises, for the same reason as its sibling: a workspace whose
    tools cannot be read is a workspace with no MCP servers this turn.
    """
    try:
        rows = await db_client.get_tools_for_organization(
            organization_id, status=ToolStatus.ACTIVE.value
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read MCP tools for org {}: {}", organization_id, exc)
        return []
    return [t for t in rows if getattr(t, "category", None) == ToolCategory.MCP.value]


def _description(tool: Any, generated: dict[str, Any]) -> str:
    verb = "reads" if is_read(tool) else "proposes a card for"
    app = toolkit_of(tool)
    where = f" in {app}" if app else ""
    return f"{generated['description']} ({verb} the connected app{where}.)"


def declares_its_own(tool: Any) -> bool:
    """Whether this tool already knows what it takes.

    A row created since the fields were chosen at sync time carries them, so
    its schema is small and already known -- there is nothing to defer and
    nothing to discover, and offering it by name alone would buy a round trip
    for an answer we are holding.

    A row from before that, or one somebody made by hand with no parameters,
    answers False and keeps the old behaviour exactly.
    """
    config = ((tool.definition or {}).get("config") or {}) if tool else {}
    declared = config.get("parameters")
    return isinstance(declared, list) and len(declared) > 0


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
    ready = [t for t in tools if declares_its_own(t)]
    deferred = [t for t in tools if not declares_its_own(t)]
    out = [schema_for(tool) for tool in ready]
    if not deferred:
        # Nothing left to load, so nothing is offered a way to load it. The
        # loader existing at all told the model some tool was still hidden.
        return out
    out.insert(0, load_tool_schema())
    for tool in deferred:
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


def _names_cleanly(tool: Any) -> bool:
    try:
        function_name(tool)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Connected tool row cannot be named: {}", exc)
        return False


def _named_lines(tools: list[Any]) -> tuple[list[str], int]:
    """The tools by name, grouped by app, writes before reads.

    Writes come first because a write is what a person asks for by name
    ("send it", "book it") and a write is what Decibyl denied having. If
    the cap bites, it bites the fetches.

    Returns the lines and how many tools did not fit.
    """
    writes = [t for t in tools if not is_read(t)]
    reads = [t for t in tools if is_read(t)]
    chosen = writes[:MAX_NAMED]
    chosen += reads[: max(0, MAX_NAMED - len(chosen))]
    keep = {id(t) for t in chosen}

    lines: list[str] = []
    for app in sorted({toolkit_of(t) or "connector" for t in chosen}):
        here = [t for t in chosen if (toolkit_of(t) or "connector") == app]
        for label, group in (
            ("proposes a card", [t for t in here if not is_read(t)]),
            ("runs now", [t for t in here if is_read(t)]),
        ):
            if group:
                names = ", ".join(function_name(t) for t in group)
                lines.append(f"- {app}, {label}: {names}")
    return lines, len(tools) - len(keep)


def apps_block(
    tools: list[Any],
    awaiting: list[str] | None = None,
    mcp_servers: list[str] | None = None,
) -> str:
    """The context line: which apps are connected and, by name, every tool
    they put in Decibyl's hands.

    **The names are the point.** This block used to say only how many tools
    there were, and that they were "listed by name" somewhere the model
    should call ``load_tool`` to open. Both were wrong by the time they were
    read: a tool that carries its own parameters is sent in full and needs
    no loader, and on the round after a card the model is given no tools at
    all (see MAX_TOOL_ROUNDS) -- so its only account of what it can reach is
    this block. Told there was a list elsewhere, it described one from
    memory, and told a person it had no way to send an email an hour after
    sending one. A model that is confidently wrong about its own drawer is
    worse than one with an empty drawer.

    ``awaiting`` are apps the account has connected whose tools are not
    ready yet (see :func:`awaiting_setup`). Named explicitly because the
    alternative is what happened: they were invisible here, visible to the
    connect-card path, and Decibyl reported the two as conflicting signals
    to the person who had just connected one.

    ``mcp_servers`` are connected another way entirely: an MCP tool pointing
    at somebody's server, which is how an app outside Composio's catalogue
    gets in at all. Zerodha is one, and it was invisible here because
    :func:`list_for_organization` keeps only what :func:`is_connected`
    admits, and that is Composio. So Decibyl told a person Zerodha "isn't an
    app I can connect from here" with a working Kite tool on the account.

    Named, but never as one of Decibyl's own tools: Decibyl's execute path
    is Composio's and it cannot call an MCP server. A bot can. Saying so is
    the honest version -- offering it would be this same bug pointed the
    other way, which is the one thing this block exists to stop.
    """
    # A row whose name cannot be turned into a function name is a row the
    # model was never going to be offered either (see :func:`schemas`), and
    # it must not take the whole context block down with it: this block is
    # read on every turn, and a workspace that cannot say what it has is
    # worse off than one with a tool missing from the list. Dropped here,
    # before anything is counted, so the totals and the names agree -- a
    # count that includes a tool the list does not name is the same lie in
    # smaller print.
    tools = [t for t in tools if _names_cleanly(t)]
    apps = sorted({toolkit_of(t) or "connector" for t in tools})
    pending = ""
    if awaiting:
        names = ", ".join(sorted(awaiting))
        pending = (
            f" {names} {'is' if len(awaiting) == 1 else 'are'} connected but "
            "still being set up, so there are no tools for it yet. Say that "
            "plainly if asked -- it is not a conflict and not a failure, and "
            "it usually takes under a minute. Do NOT offer a connect card for "
            "it: it is already connected."
        )
    # What to do about an app that is not connected is the model's next
    # sentence, so it is said here. It used to read "Connect one from
    # Marketplace -> Tools", and the model dutifully passed that on: the
    # person was sent out of the conversation to find a screen. There is a
    # card for this now (see connector_offer), and the card is the answer.
    offer = (
        f"To reach an app that is not connected, call {OFFER_TOOL_NAME} and a "
        "connect card goes on the thread. Never tell anybody to go to the "
        "Marketplace."
    )
    # Connected, not runnable from here. Its own sentence, because the model
    # must be able to say the app is connected without ever reaching for it.
    by_a_bot = ""
    if mcp_servers:
        names = ", ".join(sorted(mcp_servers))
        by_a_bot = (
            f" {names} {'is' if len(mcp_servers) == 1 else 'are'} connected "
            "through its own server. You cannot call it yourself, and a bot "
            "built with it can. Say it is connected and offer to build or "
            "change a bot that uses it; never say it is not connected here, "
            "and never offer a connect card for it."
        )

    if not apps:
        if by_a_bot:
            return f"No Composio app is connected.{by_a_bot} {offer}"
        if pending:
            return f"No app is usable yet.{pending} {offer}"
        return f"No apps connected. {offer}"
    reads = sum(1 for t in tools if is_read(t))
    lines, unnamed = _named_lines(tools)
    # The claim is only made when it is true. A block that says "this is
    # everything" while holding back forty names is how this bug gets built
    # a second time, with the model's trust behind it.
    scope = (
        f"{unnamed} more are on your tool list but not named here; read it "
        "before you say you cannot do something."
        if unnamed
        else "This is every tool you have: if one is named here you have it, "
        "and if it is not named here you do not."
    )
    return (
        f"Connected: {', '.join(apps)}. {len(tools)} tools ({reads} read-only). "
        "Read tools run as you answer; anything that sends, creates or changes "
        f"something proposes a card first. {scope}\n"
        + "\n".join(lines)
        + "\n"
        + " ".join(part for part in (by_a_bot.strip(), pending.strip(), offer) if part)
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
    # A large read is stored and previewed rather than truncated (Step 20),
    # so nothing is lost and a script can work through the whole of it.
    if spill.is_large(result):
        from api.services.sandbox import code_mode

        return await spill.spill_if_large(
            result,
            organization_id=organization_id,
            run_id=None,
            name=function_name(tool),
            call_id=ref_id.rsplit(":", 1)[-1],
            can_run_scripts=await code_mode.allowed(organization_id),
        )
    return _bounded(result)


__all__ = [
    "LOAD_TOOL_NAME",
    "MAX_NAMED",
    "MAX_RESULT_CHARS",
    "PREFIX",
    "READ_VERBS",
    "apps_block",
    "by_function_name",
    "execute",
    "function_name",
    "declares_its_own",
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


async def awaiting_setup(organization_id: int, tools: list[Any]) -> list[str]:
    """Apps this account has connected that still have no tool rows.

    Decibyl told the founder, in one message, that Gmail was "already
    connected" and that the workspace had "no apps connected", and then
    picked one of the two to act on. Both sentences were true of their own
    source: the connect-card path asks the vendor, and ``apps_block`` counts
    tool rows. Between connecting an app and its rows existing, the two
    disagree -- and a reader handed a contradiction reports a contradiction.

    So the gap is named rather than left to be inferred, and creating the
    missing rows is queued from here as well as from the Integrations
    screen. Hooking that only to the screen was the hole in the first fix:
    somebody who connects an app and goes straight back to the chat never
    opens it.

    Best-effort throughout: an account whose connected list cannot be read
    is reported as having no gap, which is what the old behaviour was.
    """
    from api.services.integrations.composio.client import (
        ComposioNotConfigured,
        connected_toolkits,
    )

    try:
        connected = {slug.lower() for slug in await connected_toolkits(organization_id)}
    except (ComposioNotConfigured, Exception) as exc:  # noqa: BLE001
        logger.warning(
            "Could not read connected apps for org {}: {}", organization_id, exc
        )
        return []
    if not connected:
        return []

    have = {toolkit_of(t) for t in tools if toolkit_of(t)}
    missing = sorted(connected - have)
    if not missing:
        return []

    # Queue the rows. The answer this turn still says they are not ready --
    # promising otherwise is what started this -- but the next turn has them.
    try:
        from api.tasks.arq import enqueue_job
        from api.tasks.function_names import FunctionNames

        await enqueue_job(
            FunctionNames.SYNC_MISSING_TOOLS,
            organization_id=organization_id,
            apps=missing,
            # No person triggered this: the chat noticed the gap while
            # answering. The task picks a member of the organisation.
            user_id=None,
        )
    except Exception as exc:  # noqa: BLE001 - saying it is still worth doing
        logger.warning("Could not queue tool rows for org {}: {}", organization_id, exc)
    return missing
