"""What the builder is allowed to do, and what it is deliberately not.

The MCP server exposes a larger surface to an external editor where a developer
is watching. This catalogue is smaller on purpose: the builder talks to someone
who has never seen the canvas, so every tool here either gathers information or
produces a working agent, and nothing here can spend money or destroy work.

Three exclusions are the point of the list rather than gaps in it.

**Nothing buys a phone number.** A number costs money every month, a retry buys
a second one, and releasing it is irreversible at the carrier. The builder can
show what the account already owns and what is available; the purchase happens
on the Telephony screen where a person confirms it. This mirrors the boundary
the MCP server already draws for the same reason.

**Nothing edits an existing workflow.** The builder creates; it cannot
overwrite. A chat that can silently rewrite the agent answering a clinic's
phone is a chat one bad turn away from an outage.

**Nothing writes code.** The model chooses a template and gathers answers;
`assemble` builds the graph. A model that emitted a workflow could emit an
invalid one, and a failed build in a chat aimed at a non-developer is a dead
end rather than a correction loop.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import db_client
from api.services.agent_builder.assemble import (
    AssemblyError,
    assemble,
    required_variables,
)
from api.services.agent_templates import find_templates, get_template, list_templates
from api.services.billing.addons import DEFAULT_AGENT_ADDONS
from api.services.billing.estimator import estimate_cost_per_minute
from api.services.integrations.composio.client import (
    connect_link as composio_connect_link,
)
from api.services.integrations.composio.client import (
    connected_toolkits,
)
from api.services.integrations.composio.client import (
    is_configured as composio_configured,
)
from api.services.integrations.composio.client import (
    toolkit_name as composio_toolkit_name,
)
from api.services.packs import badges, pricing
from api.services.packs.search import search_packs
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph


def _suggest_roles(query: str | None) -> dict[str, Any]:
    """Roles from the shelf that match what the user described.

    Hiring is offered before building, deliberately. A listed role carries a
    measured outcome rate across every business that hired it; an agent
    assembled from a description in a chat window is a first draft about to go
    on somebody's live phone line.

    An empty shelf is reported as such rather than as an empty list. With no
    demo number configured every calling role is unlisted, and a model that
    received `[]` would conclude we have nothing to offer and start building --
    which is the wrong answer to a configuration problem.
    """
    roles = search_packs(query or "")
    if not roles:
        return {
            "roles": [],
            "note": (
                "No ready-made role matches, or none is published yet. Say so "
                "plainly, then offer to build one from a template."
            ),
        }
    return {
        "roles": [
            {
                "slug": role.slug,
                "name": role.name,
                "job": role.job,
                "summary": role.summary,
                # The promise, not the channel name: "Answers calls" is what
                # somebody hires, "inbound_call" is how we route it.
                "does": badges(role),
                "industries": list(role.industries),
                "languages": list(role.languages),
                "needs_connected": [
                    {"app": connector.label, "for": connector.used_for}
                    for connector in role.required_connectors
                    if connector.required
                ],
                "monthly_price_rupees": pricing(role)["monthly_price_paise"] // 100,
                "priced_as": (
                    "a hire, per agent"
                    if pricing(role)["is_hire"]
                    else "included in the monthly plan"
                ),
                "demo_number": role.demo_number,
                "template_id": role.template_id,
            }
            for role in roles[:4]
        ],
        "note": (
            "Show these to the user with the price and what each needs "
            "connected. Offer the demo number so they can interview it before "
            "hiring. Build something custom only if they say none fit."
        ),
    }


def tool_schemas() -> list[dict[str, Any]]:
    """The catalogue, in OpenAI's function shape.

    That shape is the one all three vendors can be derived from without loss,
    so it is the form the adapters in `client.py` translate *from* rather than
    a fourth dialect to keep in sync.
    """
    return [
        {
            "name": "suggest_roles",
            "description": (
                "Find ready-to-hire roles that match what the user just said "
                "about their business. ALWAYS call this first, before "
                "list_agent_templates and before asking any questions. A "
                "listed role has a measured outcome rate across every "
                "business that hired it; an agent you build from scratch has "
                "none, so offering the proven one first is better for the "
                "user. Show the user the roles it returns, with the price and "
                "what each one needs connected, and let them pick. Only build "
                "something custom if they say none of them fit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What the user's business does and what they want "
                            "handled, in their own words. Omit to see the "
                            "whole shelf."
                        ),
                    }
                },
            },
        },
        {
            "name": "list_agent_templates",
            "description": (
                "List ready-made agent templates for Indian businesses. Call "
                "this first, before asking the user anything else, so you can "
                "name a concrete starting point instead of asking open "
                "questions about what they want built."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What the user's business does, in their words. "
                            "Omit to see the whole catalogue."
                        ),
                    }
                },
            },
        },
        {
            "name": "get_agent_template",
            "description": (
                "Fetch one template in full, including the questions you must "
                "ask. The returned `required_variables` are exactly what the "
                "user has to answer before the agent can be built — ask for "
                "them one at a time, in the order given."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template_id": {
                        "type": "string",
                        "description": "An id from list_agent_templates.",
                    }
                },
                "required": ["template_id"],
            },
        },
        {
            "name": "estimate_agent_cost",
            "description": (
                "Price a template's provider stack, per minute and per month. "
                "Call this once the template is chosen and tell the user the "
                "figures unprompted — knowing the cost before the first call "
                "is something no competing platform offers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template_id": {
                        "type": "string",
                        "description": "Template whose recommended stack to price.",
                    },
                    "calls_per_month": {
                        "type": "integer",
                        "description": (
                            "The user's expected monthly call volume, if they "
                            "have said. Omit to use the template's typical "
                            "figure."
                        ),
                    },
                },
                "required": ["template_id"],
            },
        },
        {
            "name": "list_phone_numbers",
            "description": (
                "List the phone numbers this account already owns, so the user "
                "can pick one to put the agent on. If the list is empty, tell "
                "them they will need a number and point them at the Telephony "
                "screen — you cannot buy one for them."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "create_agent",
            "description": (
                "Build the agent. Only call this once you have every value in "
                "`required_variables` — a missing one is reported back rather "
                "than guessed, and the agent is not created. On success the "
                "agent exists and can be opened and tested immediately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template_id": {"type": "string"},
                    "name": {
                        "type": "string",
                        "description": (
                            "What to call this agent, in the user's words. "
                            "e.g. 'Sunrise Clinic front desk'."
                        ),
                    },
                    "variables": {
                        "type": "object",
                        "description": (
                            "The user's answers, keyed by the variable names "
                            "from required_variables."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["template_id", "name", "variables"],
            },
        },
        {
            "name": "list_my_agents",
            "description": (
                "List the agents this account already has, with whether each "
                "one can be revised here. Call this when the user talks about "
                "changing something rather than building something."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "revise_agent_facts",
            "description": (
                "Change the facts an agent was built with -- its hours, "
                "address, prices, the names of its staff. Saves a DRAFT; the "
                "live agent keeps answering exactly as before until a person "
                "opens it and publishes. Tell the user that, every time. "
                "Only supply the values that are changing; everything else is "
                "kept. Only works on agents built from a template here."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "integer"},
                    "variables": {
                        "type": "object",
                        "description": "Only the values that change.",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["workflow_id", "variables"],
            },
        },
        {
            "name": "list_connected_apps",
            "description": (
                "List the outside apps this account has already connected -- "
                "Gmail, Google Sheets, Slack and so on. Call this before "
                "offering to connect anything, so you never ask a user to "
                "connect something they connected last week."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "connect_app",
            "description": (
                "Give the user a link to connect one outside app to this "
                "account, so their agent can use it. Returns a URL -- show it "
                "to them and ask them to open it and sign in. You cannot "
                "complete the connection yourself; only they can, and only in "
                "a browser. The link expires in a few minutes, so call this "
                "when they are ready rather than in advance. After they say "
                "they are done, call list_connected_apps to check."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": (
                            "The app's Composio slug, lowercase, e.g. gmail, "
                            "googlesheets, googlecalendar, slack, notion, "
                            "hubspot. If you are not sure of the exact slug, "
                            "say so and ask the user which app they mean "
                            "rather than guessing -- a wrong slug is refused."
                        ),
                    },
                },
                "required": ["app"],
            },
        },
    ]


#: Where the template id is kept inside ``template_context_variables``. Prefixed
#: so it can never collide with a template's own variable name.
PROVENANCE_TEMPLATE_KEY = "__template_id"


TOOL_NAMES = frozenset(t["name"] for t in tool_schemas())


async def dispatch(
    name: str,
    arguments: dict[str, Any],
    *,
    session: AsyncSession,
    organization_id: int,
    user_id: int,
) -> dict[str, Any]:
    """Run one tool call.

    Never raises. Every failure comes back as a dict carrying an ``error`` the
    model can read and act on, because a raised exception ends the session
    where an error message lets it recover — usually by asking the user
    something it skipped.
    """
    try:
        if name == "suggest_roles":
            return _suggest_roles(arguments.get("query"))
        if name == "list_agent_templates":
            return _list_templates(arguments.get("query"))
        if name == "get_agent_template":
            return _get_template(arguments.get("template_id", ""))
        if name == "estimate_agent_cost":
            return await _estimate(
                session,
                organization_id=organization_id,
                template_id=arguments.get("template_id", ""),
                calls_per_month=arguments.get("calls_per_month"),
            )
        if name == "list_phone_numbers":
            return await _list_numbers(organization_id)
        if name == "list_my_agents":
            return await _list_my_agents(organization_id)
        if name == "revise_agent_facts":
            return await _revise_agent_facts(
                organization_id=organization_id,
                workflow_id=arguments.get("workflow_id"),
                variables=arguments.get("variables") or {},
            )
        if name == "list_connected_apps":
            return await _list_connected_apps(organization_id)
        if name == "connect_app":
            return await _connect_app(
                organization_id=organization_id,
                app=arguments.get("app", ""),
            )
        if name == "create_agent":
            return await _create_agent(
                organization_id=organization_id,
                user_id=user_id,
                template_id=arguments.get("template_id", ""),
                name=arguments.get("name", ""),
                variables=arguments.get("variables") or {},
            )
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.exception("Agent builder tool {} failed", name)
        return {"error": f"{name} failed: {exc}"}

    return {"error": f"Unknown tool {name!r}."}


def _list_templates(query: str | None) -> dict[str, Any]:
    found = find_templates(query) if query else list_templates()
    if not found:
        found = list_templates()
    return {
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "vertical": t.vertical,
                "direction": t.direction.value,
                "summary": t.summary,
                "languages": t.languages,
            }
            for t in found
        ]
    }


def _get_template(template_id: str) -> dict[str, Any]:
    template = get_template(template_id)
    if template is None:
        return {
            "error": f"No template {template_id!r}. Call list_agent_templates.",
        }
    return {
        "id": template.id,
        "name": template.name,
        "summary": template.summary,
        "direction": template.direction.value,
        "languages": template.languages,
        "stack": template.stack.model_dump(),
        # What the chat has to ask about. Derived from the prompts, so it
        # cannot drift from what the agent will actually say.
        "required_variables": [
            {"name": key, "asks_for": template.template_variables.get(key, key)}
            for key in required_variables(template)
        ],
        "compliance_notes": template.compliance_notes,
        "typical_call_seconds": template.call_seconds,
        "typical_calls_per_month": template.calls_per_month,
    }


async def _estimate(
    session: AsyncSession,
    *,
    organization_id: int,
    template_id: str,
    calls_per_month: int | None,
) -> dict[str, Any]:
    template = get_template(template_id)
    if template is None:
        return {"error": f"No template {template_id!r}."}

    # An agent that never speaks has no minutes, so a per-minute quote would
    # be a number with no unit behind it. The honest answer is the plan it runs
    # inside, and that reads better to an owner than a rupee figure rounding to
    # zero would.
    if not template.speaks:
        return {
            "priced_as": "included in the monthly plan",
            "rupees_per_minute": None,
            "monthly": None,
            "note": (
                "This agent makes no calls, so it uses no minutes. It runs "
                "against the tasks included in the monthly plan. Tell the "
                "user that plainly rather than quoting a per-minute figure."
            ),
        }

    stack = template.stack
    estimate = await estimate_cost_per_minute(
        session,
        organization_id=organization_id,
        # Every agent this quotes for is created with a QA node, so leaving it
        # out quoted below what the first invoice would say.
        addons=DEFAULT_AGENT_ADDONS,
        stt_provider=stack.stt_provider,
        stt_model=stack.stt_model,
        llm_provider=stack.llm_provider,
        llm_model=stack.llm_model,
        tts_provider=stack.tts_provider,
        tts_model=stack.tts_model,
        telephony_provider=stack.telephony_provider,
    )

    per_minute = estimate.total_paise_per_minute / 100
    calls = calls_per_month or template.calls_per_month or 0
    minutes = round(calls * (template.call_seconds or 0) / 60)

    result: dict[str, Any] = {
        "rupees_per_minute": round(per_minute, 2),
        "monthly": {
            "calls": calls,
            "minutes": minutes,
            "rupees": round(per_minute * minutes, 0),
        },
        "stack_rationale": stack.rationale,
        "pulse_seconds": estimate.pulse_seconds,
    }
    if estimate.unpriced:
        result["unpriced"] = list(estimate.unpriced)
        result["warning"] = (
            "Some components have no rate on file, so this is an "
            "underestimate. Say so when quoting it."
        )
    return result


async def _list_numbers(organization_id: int) -> dict[str, Any]:
    """The account's numbers, across every telephony configuration it has.

    Numbers hang off a configuration rather than off the organization, so this
    walks the configurations — the same traversal the MCP telephony tool does,
    and the reason there is no one-call org-level lookup to use instead.
    """
    numbers: list[dict[str, Any]] = []
    configurations = await db_client.list_telephony_configurations(organization_id)
    for configuration in configurations or []:
        rows = await db_client.list_phone_numbers_for_config(configuration.id)
        for row in rows or []:
            numbers.append(
                {
                    "phone_number_id": row.id,
                    "address": row.address,
                    "label": row.label,
                    "is_active": row.is_active,
                    "inbound_workflow_id": row.inbound_workflow_id,
                }
            )

    return {
        "numbers": numbers,
        "note": (
            "If this list is empty the account has no number yet. Tell the "
            "user to add one in Telephony — you cannot buy one for them."
        )
        if not numbers
        else (
            "Attaching the agent to a number happens on the Telephony screen; "
            "you cannot do it here."
        ),
    }


async def _create_agent(
    *,
    organization_id: int,
    user_id: int,
    template_id: str,
    name: str,
    variables: dict[str, str],
) -> dict[str, Any]:
    template = get_template(template_id)
    if template is None:
        return {"error": f"No template {template_id!r}."}

    try:
        built = assemble(template, name=name, variables=variables)
    except AssemblyError as exc:
        return {"error": str(exc)}

    if built.missing_variables:
        # Not an error the user should see as a failure — it means the chat
        # skipped a question. Reported so the model asks it.
        return {
            "created": False,
            "missing_variables": built.missing_variables,
            "error": (
                "Cannot build yet: still missing "
                f"{', '.join(built.missing_variables)}. Ask the user for these "
                "one at a time, then call create_agent again."
            ),
        }

    # Validate exactly as every other creation path does, before anything is
    # written. A template that produced an invalid graph is our bug, and it
    # should surface here rather than as a broken agent in the canvas.
    try:
        dto = ReactFlowDTO.model_validate(built.definition)
        WorkflowGraph(dto)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Assembled template {} did not validate", template_id)
        return {"error": f"The assembled agent did not validate: {exc}"}

    workflow = await db_client.create_workflow(
        name=built.name,
        workflow_definition=built.definition,
        user_id=user_id,
        organization_id=organization_id,
    )

    # Which template, and the answers that filled it. Without this a later
    # revision would have to infer both from the assembled prompts, which is
    # guessing at something we knew for certain a moment ago.
    await db_client.update_workflow(
        workflow_id=workflow.id,
        name=None,
        workflow_definition=None,
        template_context_variables={
            PROVENANCE_TEMPLATE_KEY: template_id,
            **variables,
        },
        workflow_configurations=None,
        organization_id=organization_id,
    )

    return {
        "created": True,
        "workflow_id": workflow.id,
        "name": built.name,
        "open_url": f"/workflow/{workflow.id}",
        "next_steps": [
            "Tell the user the agent is built and can be opened and tested now.",
            "Remind them to attach a phone number in Telephony before it can "
            "take real calls.",
        ],
    }


async def _list_my_agents(organization_id: int) -> dict[str, Any]:
    """What this account already has, and which of them this chat can revise.

    ``revisable`` is the honest half. An agent built before provenance was
    recorded, or built on the canvas rather than here, cannot be revised by
    re-filling variables that were never stored -- and a chat that offers to
    change one and then cannot is worse than one that says so first.
    """
    # Scoped, and the full row rather than the listing projection: the
    # listing one drops template_context_variables, which is the column
    # that decides whether an agent can be revised at all.
    workflows = await db_client.get_all_workflows(organization_id=organization_id)
    agents = []
    for workflow in workflows or []:
        stored = getattr(workflow, "template_context_variables", None) or {}
        template_id = stored.get(PROVENANCE_TEMPLATE_KEY)
        agents.append(
            {
                "workflow_id": workflow.id,
                "name": workflow.name,
                "revisable": bool(template_id and get_template(template_id)),
                "open_url": f"/workflow/{workflow.id}",
            }
        )
    return {
        "agents": agents,
        "note": (
            "An agent that is not revisable was not built from a template "
            "here. Point the user at its editor rather than offering to "
            "change it."
        ),
    }


async def _revise_agent_facts(
    *,
    organization_id: int,
    workflow_id: Any,
    variables: dict[str, str],
) -> dict[str, Any]:
    """Re-fill an agent's facts and save the result as a draft.

    Never touches what is answering the phone. The platform already separates
    a draft from the published version, and a person publishes -- which is the
    same boundary the rest of this catalogue draws around money and destruction,
    applied to the one thing a chat could otherwise break silently.
    """
    if not isinstance(workflow_id, int):
        return {"error": "workflow_id must be the number from list_my_agents."}
    if not variables:
        return {"error": "Nothing to change. Ask the user what should differ."}

    # Scoped. `get_workflow_by_id` exists and is unscoped, and its own
    # docstring says never to call it with a request-supplied id on a
    # user-facing path -- and a workflow_id a model produced is exactly
    # that, however it came by it.
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        return {"error": f"No agent {workflow_id} in this account."}

    stored = dict(getattr(workflow, "template_context_variables", None) or {})
    template_id = stored.pop(PROVENANCE_TEMPLATE_KEY, None)
    template = get_template(template_id) if template_id else None
    if template is None:
        return {
            "error": (
                "This agent was not built from a template here, so its facts "
                "cannot be changed from this chat. Open it in the editor "
                f"instead: /workflow/{workflow_id}"
            )
        }

    # The stored answers are the base; only what the user changed is replaced.
    # A caller who says "the new number is X" must not silently blank the
    # address by omitting it.
    merged = {**stored, **{k: v for k, v in variables.items() if v and v.strip()}}

    try:
        built = assemble(template, name=workflow.name, variables=merged)
    except AssemblyError as exc:
        return {"error": str(exc)}
    if built.missing_variables:
        return {
            "revised": False,
            "missing_variables": built.missing_variables,
            "error": (
                "Cannot rebuild yet: still missing "
                f"{', '.join(built.missing_variables)}. Ask for these, then "
                "call revise_agent_facts again."
            ),
        }

    try:
        dto = ReactFlowDTO.model_validate(built.definition)
        WorkflowGraph(dto)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Revised template {} did not validate", template_id)
        return {"error": f"The revised agent did not validate: {exc}"}

    await db_client.update_workflow(
        workflow_id=workflow_id,
        name=None,
        workflow_definition=built.definition,
        template_context_variables={
            PROVENANCE_TEMPLATE_KEY: template_id,
            **merged,
        },
        workflow_configurations=None,
        organization_id=organization_id,
    )
    return {
        "revised": True,
        "workflow_id": workflow_id,
        "changed": sorted(variables),
        "open_url": f"/workflow/{workflow_id}",
        "next_steps": [
            "Say plainly that this is saved as a draft and the live agent is "
            "still answering exactly as it did before.",
            "Tell them to open the agent, test it, and publish when happy.",
        ],
    }


async def _list_connected_apps(organization_id: int) -> dict[str, Any]:
    """Which outside apps this account has authorized.

    A deployment with no Composio key is not an error to report upward -- it is
    a platform that simply does not offer this, and the builder should stop
    talking about it rather than tell a clinic owner about a missing
    environment variable.
    """
    if not composio_configured():
        return {
            "apps": [],
            "available": False,
            "note": (
                "Connecting outside apps is not switched on for this "
                "platform. Do not offer it."
            ),
        }

    apps = await connected_toolkits(organization_id)
    return {
        "apps": apps,
        "available": True,
        "note": (
            "Nothing is connected yet. If the user wants their agent to send "
            "email, update a sheet or post to Slack, offer connect_app."
            if not apps
            else (
                "These are already connected; do not ask the user to connect "
                "them again."
            )
        ),
    }


async def _connect_app(*, organization_id: int, app: str) -> dict[str, Any]:
    """A link the user opens to authorize one app.

    Two refusals before any link is minted, and both are deliberate.

    The slug is checked against Composio's own catalogue rather than a list
    kept here: a hardcoded list goes stale the week they add an app, and the
    failure mode of a stale list is telling a user we cannot do something we
    can. The failure mode of an unchecked slug is worse -- an auth config
    created against an invented app, and a confusing failure later.

    And an app this organization already connected returns no link at all.
    Minting a second one is how a user ends up with two authorizations and no
    idea which one their agent uses.
    """
    if not composio_configured():
        return {
            "error": ("Connecting outside apps is not switched on for this platform.")
        }

    slug = (app or "").strip().lower()
    if not slug:
        return {"error": "Which app? Ask the user, then call this again."}

    already = await connected_toolkits(organization_id)
    if slug.upper() in already:
        return {
            "already_connected": True,
            "app": slug,
            "note": (
                f"{slug} is already connected to this account. Tell the user "
                "it is ready to use; do not send them a link."
            ),
        }

    display_name = await composio_toolkit_name(slug)
    if not display_name:
        return {
            "error": (
                f"There is no app called {slug!r}. Ask the user which app "
                "they mean by name and try the obvious slug for it."
            )
        }

    link = await composio_connect_link(toolkit=slug, organization_id=organization_id)
    if "error" in link:
        return link

    return {
        "app": slug,
        "app_name": display_name,
        "connect_url": link["url"],
        "expires_at": link.get("expires_at"),
        "note": (
            f"Show this link to the user and ask them to open it and sign in "
            f"to {display_name}. It expires in a few minutes. You cannot "
            f"complete this for them. When they say they have finished, call "
            f"list_connected_apps to confirm it worked."
        ),
    }
