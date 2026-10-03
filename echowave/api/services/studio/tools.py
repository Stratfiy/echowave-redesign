"""What the Studio chat can do: make agents, connect them to everything the
account has, and make a website for them.

Two halves. The agent half is the builder's own tools, reused by name, so an
agent made in Studio is assembled, validated, priced and connected exactly
like one made in the builder (``services/agent_builder/tools.py``) --
including the outside apps: connect one with a link in the thread, list what
it can do, and attach that action to an agent. The site half is new: create
a site, theme it, find photos for it, write its files, build it in the
sandbox, look at screenshots of it, put agents on it, and send its contact
form to an agent.

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
from api.services.studio import images, sites, themes
from api.services.studio.scaffold import FRAMEWORKS

#: The builder's tools Studio offers, by name. Checked against the builder's
#: catalogue at import (below): a rename there fails here, loudly, rather than
#: quietly leaving Studio unable to make agents.
AGENT_TOOLS: tuple[str, ...] = (
    "suggest_roles",
    "set_business_type",
    "list_agent_templates",
    "get_agent_template",
    "estimate_agent_cost",
    "create_agent",
    "list_my_agents",
    "list_voice_and_brain",
    "set_voice_and_brain",
    "revise_agent_prompt",
    "revise_agent_facts",
    "list_phone_numbers",
    # Everything the account connects to, through Composio: what is connected,
    # a link to connect more, what each app can do, and giving an agent one
    # of those actions.
    "list_connected_apps",
    "connect_app",
    "list_app_actions",
    "list_app_accounts",
    "attach_app_tool",
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
            "Start a new website, already designed: React + Vite + Tailwind "
            "CSS v4, a theme, and finished sections (Navbar, Hero, Features, "
            "Stats, Steps, Testimonials, Pricing, FAQ, CTA, Contact, Footer) "
            "that render the content in src/site.js. Call this once per "
            "website, not per change."
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
                "theme": {
                    "type": "string",
                    "description": "A name from list_design_themes.",
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
    {
        "name": "list_design_themes",
        "description": (
            "List the design themes: a palette and a font pair each, chosen "
            "for a kind of business and checked for readable contrast. Pick "
            "the one that fits the business before writing the site."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "apply_design_theme",
        "description": (
            "Apply a theme to a site: its colours become the Tailwind tokens "
            "(bg-brand, text-ink, text-muted, bg-surface, bg-canvas, ring-line, "
            "text-brand-ink on brand) and its typefaces font-display and "
            "font-body. Build afterwards."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": _SITE_ID,
                "theme": {
                    "type": "string",
                    "description": "A name from list_design_themes.",
                },
            },
            "required": ["site_id", "theme"],
        },
    },
    {
        "name": "find_images",
        "description": (
            "Search openly licensed photos that may be used on a business "
            "site. Returns image URLs with their size and the credit each "
            "needs. Use the url as an <img src>, give it real alt text, and "
            "add each credit to site.credits in src/site.js when needs_credit "
            "is true. Search in plain English for what the photo shows "
            "('smiling dentist with patient', 'fresh bread on wooden table')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "orientation": {"type": "string", "enum": ["wide", "tall", "square"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "review_site_design",
        "description": (
            "Look at the site: returns screenshots of the last successful "
            "build on a laptop (1280px) and a phone (390px), any script "
            "errors the page threw, and whether it scrolls sideways. Call it "
            "after every successful build of a new site or a visible change, "
            "judge the pictures against the checklist it returns, and fix "
            "what is off before telling the user it is ready."
        ),
        "parameters": {
            "type": "object",
            "properties": {"site_id": _SITE_ID},
            "required": ["site_id"],
        },
    },
    {
        "name": "connect_form_to_agent",
        "description": (
            "Send the site's contact form to an agent: each enquiry starts "
            "that agent with the instruction you give, and it acts with the "
            "tools attached to it (for example a WhatsApp or email action "
            "from attach_app_tool, or a calendar). Ask the user what should "
            "happen to an enquiry, then call this. Build afterwards."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": _SITE_ID,
                "workflow_id": {"type": "integer", "description": "The agent."},
                "instruction": {
                    "type": "string",
                    "description": (
                        "What the agent does with each enquiry, e.g. 'Reply on "
                        "WhatsApp within a minute, thank them by name, and "
                        "offer the next three free slots.'"
                    ),
                },
            },
            "required": ["site_id", "workflow_id", "instruction"],
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
            theme=str(arguments.get("theme") or themes.DEFAULT_THEME),
        )
        return {"created": True, **sites.summary(site)}
    if name == "list_design_themes":
        return {"themes": themes.catalogue()}
    if name == "find_images":
        try:
            found = await images.search(
                str(arguments.get("query") or ""),
                orientation=arguments.get("orientation"),
            )
        except images.ImageSearchError as exc:
            raise sites.SiteError(str(exc)) from exc
        return {
            "images": found,
            "note": "No photos matched; try simpler words." if not found else "",
        }

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
    if name == "apply_design_theme":
        return await sites.apply_theme(
            site,
            organization_id=organization_id,
            theme_name=str(arguments.get("theme") or ""),
        )
    if name == "review_site_design":
        return await sites.review_design(site)
    if name == "connect_form_to_agent":
        return await sites.connect_form(
            site,
            organization_id=organization_id,
            user_id=user_id,
            workflow_id=arguments.get("workflow_id"),
            instruction=str(arguments.get("instruction") or ""),
        )
    raise sites.SiteError(f"Unknown tool {name!r}.")  # pragma: no cover
