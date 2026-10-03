"""What the Studio chat can do: make agents, and make a website for them.

Two halves. The agent half is the builder's own tools, reused by name, so an
agent made in Studio is assembled, validated and priced exactly like one made
in the builder (``services/agent_builder/tools.py``). The site half is new:
create a site, read and write its files, build it in the sandbox, and put
agents on it.

The same boundaries as the builder hold, for the same reasons: nothing here
buys a number, publishes an agent or deletes a site. Writing files is the one
thing the builder deliberately cannot do and this can -- but what is written
is a website's source, which the sandbox builds and the build either passes
or reports why not, so a bad write is a correction loop rather than an outage.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import db_client
from api.services.agent_builder import tools as builder_tools
from api.services.studio import sites
from api.services.studio.scaffold import FRAMEWORKS

#: The builder's tools Studio offers, by name. Checked against the builder's
#: catalogue at import (below): a rename there fails here, loudly, rather than
#: quietly leaving Studio unable to make agents.
AGENT_TOOLS: tuple[str, ...] = (
    "list_agent_templates",
    "get_agent_template",
    "estimate_agent_cost",
    "create_agent",
    "list_my_agents",
    "list_voice_and_brain",
    "set_voice_and_brain",
    "revise_agent_prompt",
    "revise_agent_facts",
)

_builder_schemas = {schema["name"]: schema for schema in builder_tools.tool_schemas()}
_missing = [name for name in AGENT_TOOLS if name not in _builder_schemas]
if _missing:  # pragma: no cover - a build-time guard
    raise RuntimeError(f"Studio reuses builder tools that no longer exist: {_missing}")

_SITE_ID = {"type": "integer", "description": "The site's id."}

SITE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_sites",
        "description": "List this workspace's sites, newest first.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "create_site",
        "description": (
            "Start a new website. It begins as a working React app built by "
            "Vite (index.html, vite.config.js, src/main.jsx, src/App.jsx, "
            "src/index.css, package.json) which you then edit. Call this once "
            "per website, not per change."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The site's name, also its page title.",
                },
                "framework": {
                    "type": "string",
                    "enum": list(FRAMEWORKS),
                    "description": "Leave as the default.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_site_files",
        "description": "List a site's files with their sizes, and its build state.",
        "parameters": {
            "type": "object",
            "properties": {"site_id": _SITE_ID},
            "required": ["site_id"],
        },
    },
    {
        "name": "read_site_file",
        "description": "Read one file of a site. Read before you change a file you did not just write.",
        "parameters": {
            "type": "object",
            "properties": {"site_id": _SITE_ID, "path": {"type": "string"}},
            "required": ["site_id", "path"],
        },
    },
    {
        "name": "write_site_files",
        "description": (
            "Create or replace files in a site, and optionally delete some, in "
            "one batch. Each file is written whole: send its complete new "
            "content, never a diff or a fragment. To add an npm package, "
            "rewrite package.json with it in dependencies. Applied all or "
            "nothing: if one path is refused, nothing is written."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": _SITE_ID,
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative path, e.g. src/components/Hero.jsx",
                            },
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
                "delete": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["site_id", "files"],
        },
    },
    {
        "name": "build_site",
        "description": (
            "Install the site's packages and build it in the sandbox (usually "
            "10 to 60 seconds). On success it returns the preview link. On "
            "failure it returns the errors: fix the files they name and build "
            "again. Always build after writing files, before telling the user "
            "the site is ready."
        ),
        "parameters": {
            "type": "object",
            "properties": {"site_id": _SITE_ID},
            "required": ["site_id"],
        },
    },
    {
        "name": "put_agents_on_site",
        "description": (
            "Put one or more agents on a site as a chat-and-voice widget. "
            "Needs the domains the site will be published on, which only the "
            "user knows: ask, never guess. The widget only runs on those "
            "domains. Replaces the site's previous set of agents. Build the "
            "site again afterwards."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": _SITE_ID,
                "workflow_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "The agents, by the workflow_id create_agent returned.",
                },
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "e.g. ['sunrisedental.in', 'www.sunrisedental.in']",
                },
            },
            "required": ["site_id", "workflow_ids", "domains"],
        },
    },
]

SITE_TOOL_NAMES = frozenset(tool["name"] for tool in SITE_TOOLS)


def tool_schemas() -> list[dict[str, Any]]:
    return [_builder_schemas[name] for name in AGENT_TOOLS] + SITE_TOOLS


async def dispatch(
    name: str,
    arguments: dict[str, Any],
    *,
    session: AsyncSession,
    organization_id: int,
    user_id: int,
) -> dict[str, Any]:
    """Run one tool call. Never raises: a failure is a message the model
    reads and recovers from, as in the builder."""
    if name in AGENT_TOOLS:
        return await builder_tools.dispatch(
            name,
            arguments,
            session=session,
            organization_id=organization_id,
            user_id=user_id,
        )
    if name not in SITE_TOOL_NAMES:
        return {"error": f"Unknown tool {name!r}."}
    try:
        return await _dispatch_site_tool(
            name, arguments, organization_id=organization_id, user_id=user_id
        )
    except sites.SiteError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.exception("Studio tool {} failed", name)
        return {"error": f"{name} failed: {exc}"}


async def _dispatch_site_tool(
    name: str, arguments: dict[str, Any], *, organization_id: int, user_id: int
) -> dict[str, Any]:
    if name == "list_sites":
        rows = await db_client.list_site_projects(organization_id)
        return {"sites": [sites.summary(row, include_files=False) for row in rows]}

    if name == "create_site":
        site = await sites.create_site(
            organization_id=organization_id,
            user_id=user_id,
            name=str(arguments.get("name") or ""),
            framework=str(arguments.get("framework") or FRAMEWORKS[0]),
        )
        return {"created": True, **sites.summary(site)}

    site = await sites.get_site(
        arguments.get("site_id"), organization_id=organization_id
    )

    if name == "list_site_files":
        return sites.summary(site)
    if name == "read_site_file":
        path = str(arguments.get("path") or "")
        return {"path": path, "content": sites.read_file(site, path)}
    if name == "write_site_files":
        files = arguments.get("files") or []
        if not isinstance(files, list):
            raise sites.SiteError("files must be a list of {path, content}.")
        deletes = arguments.get("delete") or []
        if not isinstance(deletes, list):
            raise sites.SiteError("delete must be a list of paths.")
        return await sites.write_files(
            site, organization_id=organization_id, writes=files, deletes=deletes
        )
    if name == "build_site":
        site, outcome = await sites.build_site(site, organization_id=organization_id)
        return outcome.as_result(site)
    if name == "put_agents_on_site":
        workflow_ids = arguments.get("workflow_ids") or []
        domains = arguments.get("domains") or []
        if not isinstance(workflow_ids, list) or not isinstance(domains, list):
            raise sites.SiteError("workflow_ids and domains must be lists.")
        return await sites.put_agents_on_site(
            site,
            organization_id=organization_id,
            user_id=user_id,
            workflow_ids=workflow_ids,
            domains=[str(d) for d in domains],
        )
    raise sites.SiteError(f"Unknown tool {name!r}.")  # pragma: no cover
