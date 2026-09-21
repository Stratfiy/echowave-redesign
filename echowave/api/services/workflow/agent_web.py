"""The web on any agent (OP-1): one tool row, two functions, metered.

D-1b gave Decibyl a search on the platform's Serper key and a page fetch of
our own. This gives the same two to every agent a person builds, because
the pilot's Outbound Prospecting agent finds its leads on the web and a
bot that cannot search has to be told everything.

**One row on the workspace.** The web is a built-in, like the calculator:
a ``tools`` row of category ``web`` with nothing to configure, put on a
node's ``tool_uuids`` like any other tool. ``ensure_tool`` makes the
workspace's row the first time something needs it, so a brief that says
"search the web" attaches it without anybody creating one by hand, and
the tool picker in the editor shows it beside the calendar and the
calculator.

**Same tools, same price.** The functions are ``web_tools.search`` and
``web_tools.fetch``, unchanged: the social networks refused, robots
honoured, the size cut, and a search charged as a tool call plus the
vendor's price at cost, keyed on the tool call's id so a retried turn
pays once. Charged against the agent's ``workflow_id`` so the budget on
that agent, and the per-agent unit-economics screen, both see it.

**Search only on a voice call.** A caller waits for a search the way they
wait for a calendar lookup; a page of text read into a voice context is
a wall of words the model reads out. Text and channel runs get both.

Behind the same flag as Decibyl's own web tools: they share the key, the
vendor and the rate.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from api.enums import ToolCategory, ToolStatus
from api.services.workflow import web_tools

TOOL_NAME = "Web search"
DESCRIPTION = (
    "Search the web and read pages, on the platform's key. Each search is a "
    "tool call plus the search itself; a page read is a tool call."
)

#: Words a brief uses when it means the web. Whole words, the way
#: ``brief_apps`` matches an app's name: a bot is given the web because the
#: brief said so, not because a model thought it might help.
WEB_WORDS = (
    "web",
    "website",
    "websites",
    "internet",
    "online",
    "google",
    "search engine",
    "look up",
    "lookup",
    "research",
    "url",
    "urls",
    "webpage",
    "webpages",
)


def enabled() -> bool:
    return web_tools.enabled()


def definition() -> dict[str, Any]:
    return {"schema_version": 1, "type": ToolCategory.WEB.value}


def is_web_tool(tool: Any) -> bool:
    return getattr(tool, "category", None) == ToolCategory.WEB.value


def limits_of(tool: Any) -> dict[str, Any]:
    """The tool row's allow-list and page cap (OP-2), as keyword arguments
    for ``web_tools``: ``allowed_domains`` and ``max_pages``."""
    definition_ = getattr(tool, "definition", None) or {}
    config = definition_.get("config") if isinstance(definition_, dict) else None
    config = config if isinstance(config, dict) else {}
    domains = config.get("allowed_domains") or []
    cap = config.get("max_pages_per_run")
    try:
        cap = int(cap) if cap else None
    except (TypeError, ValueError):
        cap = None
    return {
        "allowed_domains": [str(d) for d in domains if d]
        if isinstance(domains, list)
        else [],
        "max_pages": cap,
    }


def function_schemas(*, voice: bool) -> list[dict[str, Any]]:
    """The raw ``{"type": "function", "function": ...}`` schemas, in the shape
    the calculator hands the tool manager."""
    from api.services.workflow import prospects

    raw = [web_tools.search_tool_schema()]
    if not voice:
        raw.append(web_tools.fetch_tool_schema())
        # Saving what was found (OP-4) goes with reading it; a caller on the
        # phone is not building a list.
        raw.append(prospects.tool_schema())
    return [{"type": "function", "function": schema} for schema in raw]


def mentions_web(spec: str) -> bool:
    """Whether a brief names the web, on whole words."""
    text = (spec or "").lower()
    if not text:
        return False
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text)
        for word in WEB_WORDS
    )


async def ensure_tool(*, organization_id: int, user_id: int) -> str | None:
    """The workspace's web tool row, made if it has none. Its uuid, or None
    when the flag is off or the row cannot be made."""
    if not enabled():
        return None
    from api.db import db_client

    existing = await db_client.get_tools_for_organization(
        organization_id,
        status=ToolStatus.ACTIVE.value,
        category=ToolCategory.WEB.value,
    )
    for row in existing:
        return str(row.tool_uuid)
    created = await db_client.create_tool(
        organization_id=organization_id,
        user_id=user_id,
        name=TOOL_NAME,
        definition=definition(),
        category=ToolCategory.WEB.value,
        description=DESCRIPTION,
        icon="globe",
        icon_color="#2563EB",
    )
    return str(created.tool_uuid)


async def attach_if_named(
    definition_: dict[str, Any],
    *,
    organization_id: int,
    user_id: int,
    spec: str,
) -> dict[str, Any]:
    """Put the web tool on a built bot's calling nodes when its brief names
    the web. Never raises: the bot is the deliverable."""
    if not enabled() or not mentions_web(spec):
        return definition_
    try:
        uuid = await ensure_tool(organization_id=organization_id, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not give the built bot the web: {}", exc)
        return definition_
    if not uuid:
        return definition_
    from api.services.workflow import brief_apps

    logger.info("Built bot gets the web: its brief names it")
    return brief_apps.attach(definition_, [uuid])


__all__ = [
    "DESCRIPTION",
    "TOOL_NAME",
    "WEB_WORDS",
    "attach_if_named",
    "definition",
    "enabled",
    "ensure_tool",
    "function_schemas",
    "is_web_tool",
    "limits_of",
    "mentions_web",
]
