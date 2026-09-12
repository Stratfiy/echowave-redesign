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

from typing import Any, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import db_client
from api.schemas.tool import CreateToolRequest
from api.services.agent_builder.assemble import (
    AssemblyError,
    assemble,
    required_variables,
)
from api.services.agent_templates import find_templates, get_template, list_templates
from api.services.billing.addons import DEFAULT_AGENT_ADDONS
from api.services.billing.estimator import estimate_cost_per_minute
from api.services.configuration import agent_options
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY as OVERRIDE_KEY,
)
from api.services.integrations.composio import skills as app_skills
from api.services.integrations.composio.client import (
    connect_link as composio_connect_link,
)
from api.services.integrations.composio.client import (
    connected_accounts as composio_connected_accounts,
)
from api.services.integrations.composio.client import (
    connected_toolkits,
)
from api.services.integrations.composio.client import (
    is_configured as composio_configured,
)
from api.services.integrations.composio.client import (
    toolkit_actions as composio_toolkit_actions,
)
from api.services.integrations.composio.client import (
    toolkit_name as composio_toolkit_name,
)
from api.services.packs import badges, charging, resolve_listed_packs
from api.services.packs import industry as pack_industry
from api.services.packs.search import search_packs
from api.services.tool_management import create_tool_for_user
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph


async def _suggest_roles(
    query: str | None, *, organization_id: int | None = None
) -> dict[str, Any]:
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
    roles = search_packs(query or "", packs=await resolve_listed_packs())

    # What this account already is, learned from the roles it has hired. A
    # clinic that types "help with payments" should see the payment role --
    # ranked under its clinic roles, not filtered away -- so this reorders and
    # never removes. Stable against the search order: `search_packs` already
    # sorted by relevance, and Python's sort keeps that within each tier.
    known = await pack_industry.resolve(organization_id)
    if roles and known.get("candidates"):
        roles = tuple(sorted(roles, key=pack_industry.rank_key(known)))

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
                # No per-role price. Hiring is included in the plan, which
                # is a fixed platform charge -- so what the model must say is
                # what RUNNING it draws on, and whether their plan allows it.
                #
                # This used to quote "Rs6,999 a month", a figure nothing in
                # billing ever charged. Telling a clinic a number we do not
                # bill is worse than telling them nothing.
                "costs": (
                    "Included in your plan. It uses credit by the minute "
                    "while it is on a call -- call estimate_agent_cost for "
                    "the per-minute figure on their own voice."
                    if charging(role)["needs_voice"]
                    else "Included in your plan. It uses a little credit each "
                    "time it runs."
                ),
                "needs_voice_on_their_plan": charging(role)["needs_voice"],
                "demo_number": role.demo_number,
                "template_id": role.template_id,
            }
            for role in roles[:4]
        ],
        # What we assumed, so the model can say it rather than act on it
        # quietly. An inference that is wrong and invisible is the failure this
        # codebase keeps finding; one that is wrong and spoken gets corrected
        # in a word.
        "assumed_about_this_business": pack_industry.sentence(known),
        "note": (
            "Show these to the user with the price and what each needs "
            "connected. Offer the demo number so they can interview it before "
            "hiring. Build something custom only if they say none fit.\n\n"
            "If `assumed_about_this_business` is set, say it in one short "
            "sentence before the roles, and if they correct you, believe them "
            "and call `set_business_type` -- do not argue with the owner of "
            "the business about what business it is."
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
            "name": "set_business_type",
            "description": (
                "Record what kind of business this account is, when the user "
                "tells you or corrects a guess. Call it the moment they say "
                "it -- 'we're a salon, not a clinic' is the whole trigger, and "
                "a single word is enough. Never argue with the owner of a "
                "business about what business it is, and never ask this as a "
                "standalone question: it is remembered from what they hire, "
                "and asked only when they volunteer a correction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "business_type": {
                        "type": "string",
                        "description": (
                            "In the user's own words, e.g. 'dental clinic', "
                            "'D2C skincare brand', 'dental laboratory'. Do "
                            "not translate it into a category of ours."
                        ),
                    },
                },
                "required": ["business_type"],
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
            "name": "list_voice_and_brain",
            "description": (
                "The voices and the brain tiers on offer, each with what it "
                "costs a minute. Call this before asking the user how the "
                "agent should sound or how sharp it should be.\n\n"
                "Ask in these words. A voice by its name and gender, and a "
                "brain as Lite, Normal or Smart with the price a minute -- "
                "never a vendor or a model name. Quote the price without "
                "being asked: knowing what a call costs before making one is "
                "something no other platform offers."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "function",
            "name": "set_voice_and_brain",
            "description": (
                "Set one agent's voice, its brain, or both. Call this once "
                "the user has chosen -- asking which voice and then not "
                "applying it is worse than never asking.\n\n"
                "Pass only what they chose; whatever you leave out keeps its "
                "current value. Copy `brain` and `voice` exactly from "
                "list_voice_and_brain.\n\n"
                "Saves to the agent's draft and does not publish. Tell them "
                "it is not live yet and offer to let them hear it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "number",
                        "description": "The agent's id, from list_my_agents.",
                    },
                    "brain": {
                        "type": "string",
                        "description": (
                            "A `brain` from list_voice_and_brain: lite, "
                            "default or accurate. Omit to leave it alone."
                        ),
                    },
                    "voice": {
                        "type": "string",
                        "description": (
                            "A `voice` from list_voice_and_brain. Omit to "
                            "leave it alone."
                        ),
                    },
                },
                "required": ["workflow_id"],
            },
        },
        {
            "type": "function",
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
        {
            "name": "list_app_actions",
            "description": (
                "List what one connected app can be asked to do. Returns two "
                "things: `skills` we have written for voice agents -- each "
                "carrying its exact action and the rule for when to use it on "
                "a call -- and the raw `actions` from the app's own catalogue. "
                "Call this before attach_app_tool, always. Prefer a skill. "
                "Never write an action slug from memory: an invented one is "
                "accepted downstream and fails on a live call with a customer "
                "on the line."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": (
                            "The app's Composio slug from list_connected_apps, "
                            "e.g. gmail. It must already be connected."
                        ),
                    },
                },
                "required": ["app"],
            },
        },
        {
            "name": "list_app_accounts",
            "description": (
                "List the individual accounts of one connected app -- which "
                "Google Calendar, not whether Google Calendar. Call this when "
                "a business has more than one of something: three doctors' "
                "calendars, two shared inboxes, a calendar per chair. If more "
                "than one comes back, ask the user which the agent should act "
                "on; the labels are theirs and may not say whose is whose, so "
                "do not guess."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Connected app slug, e.g. googlecalendar.",
                    },
                },
                "required": ["app"],
            },
        },
        {
            "name": "attach_app_tool",
            "description": (
                "Give one agent the ability to do one thing in a connected "
                "app -- send the confirmation email, add the row to the "
                "sheet, post to the channel. Connecting an app does NOT give "
                "an agent access to it; this is the step that does, so call it "
                "after the user has connected the app and said what they want "
                "the agent to do with it.\n\n"
                "Call list_app_actions first and pass a `skill` from it "
                "whenever one fits -- it supplies the action and the rule for "
                "when to act. Fall back to a raw `action` only when nothing "
                "written covers what they asked for.\n\n"
                "Saves to the agent's draft and does not publish. Tell the "
                "user it is not live until they publish, and suggest testing "
                "first -- the action runs for real."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "number",
                        "description": "The agent's id, from list_my_agents.",
                    },
                    "app": {
                        "type": "string",
                        "description": "Connected app slug, e.g. gmail.",
                    },
                    "skill": {
                        "type": "string",
                        "description": (
                            "A `skill` from list_app_actions, e.g. "
                            "'email-the-confirmation'. Prefer this over "
                            "`action`: a skill carries the exact action and "
                            "the rule for when an agent should use it on a "
                            "live call, which is the part you must not "
                            "invent. When you pass it, `action` is not needed."
                        ),
                    },
                    "action": {
                        "type": "string",
                        "description": (
                            "An exact action slug from list_app_actions, e.g. "
                            "GMAIL_SEND_EMAIL, for when no skill fits. Do not "
                            "modify or invent it."
                        ),
                    },
                    "name": {
                        "type": "string",
                        "description": (
                            "What the agent should call this, in the user's "
                            "words, e.g. 'Email the confirmation'. The agent "
                            "reads this when deciding whether to use it."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "When the agent should use it, e.g. 'After the "
                            "booking is confirmed and the caller gave an "
                            "email address.' Optional but strongly advised."
                        ),
                    },
                    "connected_account_id": {
                        "type": "string",
                        "description": (
                            "Which account of that app to act on, from "
                            "list_app_accounts. Use it when the business has "
                            "more than one -- one tool per doctor's calendar, "
                            "named for that doctor. Omit it when there is only "
                            "one. Never invent an id."
                        ),
                    },
                },
                "required": ["workflow_id", "app", "name"],
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
            return await _suggest_roles(
                arguments.get("query"), organization_id=organization_id
            )
        if name == "set_business_type":
            return await _set_business_type(
                organization_id=organization_id,
                business_type=arguments.get("business_type", ""),
            )
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
        if name == "list_voice_and_brain":
            return await _list_voice_and_brain(session, organization_id=organization_id)
        if name == "set_voice_and_brain":
            return await _set_voice_and_brain(
                organization_id=organization_id,
                workflow_id=arguments.get("workflow_id"),
                brain=arguments.get("brain", ""),
                voice=arguments.get("voice", ""),
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
        if name == "list_app_actions":
            return await _list_app_actions(
                organization_id=organization_id,
                app=arguments.get("app", ""),
            )
        if name == "list_app_accounts":
            return await _list_app_accounts(
                organization_id=organization_id,
                app=arguments.get("app", ""),
            )
        if name == "attach_app_tool":
            return await _attach_app_tool(
                session=session,
                organization_id=organization_id,
                user_id=user_id,
                workflow_id=arguments.get("workflow_id"),
                app=arguments.get("app", ""),
                action=arguments.get("action", ""),
                name=arguments.get("name", ""),
                description=arguments.get("description", ""),
                connected_account_id=arguments.get("connected_account_id", ""),
                skill=arguments.get("skill", ""),
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


async def _list_voice_and_brain(
    session: AsyncSession, *, organization_id: int
) -> dict[str, Any]:
    """The voice and brain choices, in the words a business owner uses.

    The builder's system prompt says a clinic owner "does not know what an STT
    model is and should never be asked", and that stays true: what is offered
    here is a voice you can hear and a brain that is Lite, Normal or Smart,
    with a price a minute under each. ``agent_options`` already exists to
    offer the same choice in two vocabularies -- vendor and model on the
    Models screen, voice and brain here -- and this is the second one.

    The price is the point. A tier somebody cannot price is a tier they will
    not pick, and knowing what a call costs before making one is something no
    competitor offers, so it is quoted without being asked for.
    """
    priced: list[dict[str, Any]] = []
    for brain in agent_options.brains():
        paise = await agent_options.price_per_minute(
            session, organization_id=organization_id, brain=brain.tier
        )
        priced.append(
            {
                "brain": brain.tier,
                "label": brain.label,
                "what_it_is_for": brain.blurb,
                # None rather than zero when a component has no rate on file:
                # "we cannot price this yet" and "this is free" read the same
                # in a number and differently in a sentence.
                "rupees_per_minute": (
                    round(paise / 100, 2) if paise is not None else None
                ),
            }
        )

    return {
        "brains": priced,
        "voices": [
            {
                "voice": voice.voice_id,
                "name": voice.name,
                "gender": voice.gender,
                "is_default": voice.is_default,
            }
            for voice in agent_options.voices()
        ],
        "note": (
            "Every managed voice costs the same, so only the brain changes the "
            "price. Ask which voice by name and gender, never by vendor."
        ),
    }


async def _set_voice_and_brain(
    *,
    organization_id: int,
    workflow_id: Any,
    brain: str = "",
    voice: str = "",
) -> dict[str, Any]:
    """Set an agent's voice and brain, on its draft.

    Never touches what is answering the phone -- the same boundary
    ``revise_agent_facts`` draws, for the same reason: a chat that can change
    a live agent's voice mid-call is a chat that can do it by accident.

    Without this tool the chat could *ask* which model to use, take an answer,
    and silently not apply it, which is the exact failure this codebase keeps
    having: correct-looking behaviour with nothing behind it, and nobody finds
    out until a caller hears the wrong voice.
    """
    if not isinstance(workflow_id, int):
        return {"error": "workflow_id must be the number from list_my_agents."}

    chosen_brain = (brain or "").strip()
    chosen_voice = (voice or "").strip()
    if not chosen_brain and not chosen_voice:
        return {"error": "Nothing to set. Ask the user for a brain or a voice."}

    # Validated against what is actually offered rather than trusted. A tier
    # the model invented would be written into the stack and resolved at call
    # time to nothing.
    if chosen_brain:
        allowed = {option.tier for option in agent_options.brains()}
        if chosen_brain not in allowed:
            return {
                "error": (
                    f"{chosen_brain!r} is not a brain. Call "
                    f"list_voice_and_brain and use one of: "
                    f"{', '.join(sorted(allowed))}."
                )
            }
    if chosen_voice:
        allowed_voices = {option.voice_id for option in agent_options.voices()}
        if chosen_voice not in allowed_voices:
            return {
                "error": (
                    f"{chosen_voice!r} is not a voice we have. Call "
                    "list_voice_and_brain and copy a `voice` from it."
                )
            }

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        return {"error": f"No agent {workflow_id} in this account."}

    # Merge, never replace. Somebody who says "make it smarter" has not asked
    # for the voice to go back to default, and a partial answer is what a chat
    # message always is.
    #
    # Read straight off the stored override rather than through
    # `stack_from_configurations`, which takes a resolved configuration object
    # and not a raw column.
    stored = dict(getattr(workflow, "workflow_configurations", None) or {})
    current = stored.get(OVERRIDE_KEY)
    current_stack = current.get("stack") if isinstance(current, dict) else None
    if not isinstance(current_stack, dict):
        current_stack = {}
    tts = current_stack.get("tts") if isinstance(current_stack.get("tts"), dict) else {}
    llm = current_stack.get("llm") if isinstance(current_stack.get("llm"), dict) else {}

    override = agent_options.managed_stack_override(
        voice=chosen_voice or str(tts.get("voice") or ""),
        llm_tier=chosen_brain or str(llm.get("model") or "default"),
    )
    if not override:
        return {"error": "Nothing to set. Ask the user for a brain or a voice."}

    try:
        await db_client.save_workflow_draft(
            workflow_id, workflow_configurations={**stored, **override}
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not set voice/brain on workflow {}", workflow_id)
        return {"error": f"Could not save that: {exc}"}

    return {
        "set": True,
        "brain": chosen_brain or None,
        "voice": chosen_voice or None,
        "live": False,
        "note": (
            "Saved to the draft. Not live until they publish, and worth "
            "hearing first -- a voice reads differently out loud than it does "
            "in a list."
        ),
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


#: Nodes that can hold a tool. An agent built from a template has one talking
#: node; a graph drawn on the canvas may have several, and attaching to the
#: wrong one gives the tool to a part of the call that never needs it.
_TOOL_BEARING_NODES = ("agentNode", "conversationNode")


async def _list_app_actions(*, organization_id: int, app: str) -> dict[str, Any]:
    """What a connected app can be asked to do, from Composio's own catalogue.

    Read rather than remembered. A model asked to send email writes
    ``GMAIL_SEND``, which does not exist, and an invented slug fails at call
    time with a caller on the line -- so the choice is made from what Composio
    says the app has.

    Refuses an app this organization has not connected. Listing actions for
    something unauthorized would have the chat describe a capability, the user
    agree to it, and the attach fail one turn later.
    """
    if not composio_configured():
        return {
            "error": "Connecting outside apps is not switched on for this platform."
        }

    slug = (app or "").strip().lower()
    if not slug:
        return {"error": "Which app? Pass the slug from list_connected_apps."}

    connected = {t.lower() for t in (await connected_toolkits(organization_id))}
    if slug not in connected:
        return {
            "error": (
                f"{slug} is not connected for this account, so its actions "
                "cannot be attached yet. Offer connect_app first."
            )
        }

    # Our own, written for an agent mid-call: the exact action plus when to
    # reach for it, authored rather than improvised per customer.
    written = [
        {
            "skill": skill.slug,
            "name": skill.name,
            "does": skill.does,
            "use_when": skill.use_when,
        }
        for skill in app_skills.for_app(slug)
    ]

    actions = await composio_toolkit_actions(slug)
    if actions is None:
        # Skills still stand: they name their own action slug, so a catalogue
        # we could not read does not stop the jobs we have already written.
        # Refusing everything here would hide a working capability behind a
        # vendor outage.
        if written:
            return {
                "skills": written,
                "actions": [],
                "note": (
                    "Use one of these skills. The raw action list could not be "
                    "read just now, so do not offer anything outside them and "
                    "do not guess a slug."
                ),
            }
        return {
            "error": (
                f"Could not read what {slug} can do just now. Do not guess a "
                "slug -- say you will try again."
            )
        }
    if not actions:
        return {
            "actions": [],
            "note": (
                f"{slug} is connected but exposes nothing we can attach. Do "
                "not invent an action."
            ),
        }
    return {
        "skills": written,
        "actions": actions,
        "note": (
            (
                "Prefer a skill: each one carries the exact action and the "
                "rule for when an agent should use it on a live call, which "
                "is the part you must not invent. Pass its `skill` to "
                "attach_app_tool.\n\n"
                if written
                else ""
            )
            + "If no skill fits, pick the one action slug that matches and "
            "pass it as `action`, exactly as written. Do not modify it, and "
            "write the description as a rule about when to act."
        ),
    }


async def _set_business_type(
    *, organization_id: int, business_type: str
) -> dict[str, Any]:
    """Record what kind of business this is, because they just told us.

    The correction half of the inference. ``suggest_roles`` offers a guess
    derived from the roles an account has hired, and a guess is only safe if
    it is cheap to overturn -- one word, in their own vocabulary, and it wins
    over every later inference.

    Free text rather than a menu. Our pack industries are the shelving we use,
    not a list a business has to see itself in, and refusing "dental
    laboratory" because it is not in our enum teaches the operator that we do
    not serve them.

    Only the one field is written. Preferences arrive as a whole object and a
    save that carried schema defaults would switch unrelated settings off --
    the exact bug ``with_staff_fields`` exists for -- so this names one field
    and everything else keeps what is stored.
    """
    from api.schemas.organization_preferences import OrganizationPreferences
    from api.services.organization_preferences import (
        get_organization_preferences,
        upsert_organization_preferences,
        with_staff_fields,
    )

    value = (business_type or "").strip()
    if not value:
        return {"error": "What kind of business is it? Use their own words."}
    if len(value) > 120:
        value = value[:120]

    existing = await get_organization_preferences(organization_id)
    saved = await upsert_organization_preferences(
        organization_id,
        with_staff_fields(OrganizationPreferences(industry=value), existing),
    )
    return {
        "saved": True,
        "business_type": getattr(saved, "industry", value),
        "note": (
            "Acknowledge it in a few words and carry on with what they asked "
            "for. Do not re-ask this on later turns -- it is remembered now."
        ),
    }


async def _list_app_accounts(*, organization_id: int, app: str) -> dict[str, Any]:
    """Which *individual* account of one app this organization has authorized.

    The question a clinic with three doctors asks. ``list_connected_apps``
    answers "is Google Calendar connected"; this answers "whose", and the
    difference is the whole of per-doctor booking: three calendars authorized
    under one account identity, and "Book with Dr Ramesh" and "Book with
    Dr Priya" become two tools differing only by which one they act on.

    Deliberately not merged into one availability view. Merging three doctors'
    calendars would make a clinic look fully booked when one doctor is free,
    which is the same bug as refusing an open slot wearing a different hat.

    Composio exposes no email address -- an opaque id and a generated word-id,
    tokens redacted -- so the label is whatever the operator called it. In a
    clinic "Dr Ramesh's calendar" is a better label than a Google address
    anyway, but the model must not pretend to know which doctor is which: if
    the labels are not obvious it has to ask.
    """
    if not composio_configured():
        return {
            "error": "Connecting outside apps is not switched on for this platform."
        }

    slug = (app or "").strip().lower()
    if not slug:
        return {"error": "Which app? Pass the slug from list_connected_apps."}

    accounts = [
        account
        for account in (await composio_connected_accounts(organization_id))
        if (account.get("app") or "") == slug
    ]
    if not accounts:
        return {
            "accounts": [],
            "note": (
                f"No {slug} account is connected. Offer connect_app. Each "
                "person or resource that needs its own is connected separately."
            ),
        }
    return {
        "accounts": [
            {"id": a["connected_account_id"], "label": a["label"]} for a in accounts
        ],
        "note": (
            "If there is more than one, ask the user which it should act on "
            "rather than choosing -- the labels are theirs and may not say "
            "which person each belongs to. Pass the id as "
            "connected_account_id to attach_app_tool. Omit it only when there "
            "is exactly one and the user has not distinguished them."
        ),
    }


def _attach_to_nodes(definition: dict[str, Any], tool_uuid: str) -> int:
    """Add ``tool_uuid`` to every node that can hold one. Returns how many.

    Appended rather than replacing the list: an agent that already has a
    calendar and is given Gmail must end up with both, and a tool list that
    silently drops what was there is the failure this codebase keeps finding.
    """
    touched = 0
    for node in definition.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") not in _TOOL_BEARING_NODES:
            continue
        data = node.setdefault("data", {})
        if not isinstance(data, dict):
            continue
        existing = data.get("tool_uuids")
        uuids = list(existing) if isinstance(existing, list) else []
        if tool_uuid not in uuids:
            uuids.append(tool_uuid)
            data["tool_uuids"] = uuids
            touched += 1
    return touched


async def _attach_app_tool(
    *,
    session: AsyncSession,
    organization_id: int,
    user_id: int,
    workflow_id: Any,
    app: str,
    action: str,
    name: str,
    description: str,
    connected_account_id: str = "",
    skill: str = "",
) -> dict[str, Any]:
    """Give one agent the ability to do one thing in a connected app.

    The step the chat was missing. It could authorize Gmail and say so, and the
    agent still could not send email -- nothing attached the app to the agent,
    and nothing said so either. A user who has just agreed to connect
    something has been told a capability exists; the silence afterwards is the
    worst shape this codebase has, because it looks exactly like success.

    Writes a draft and never publishes, the same boundary ``revise_agent_facts``
    keeps: a chat that can silently change what is answering a clinic's phone
    is a chat one bad turn away from an outage. A person publishes.

    Every input a model produced is checked against the thing it names -- the
    workflow against this organization, the app against what is connected, the
    action against Composio's catalogue -- because all three arrive as strings
    the model may have invented.
    """
    if not composio_configured():
        return {
            "error": "Connecting outside apps is not switched on for this platform."
        }
    if not isinstance(workflow_id, int):
        return {"error": "workflow_id must be the number from list_my_agents."}

    slug = (app or "").strip().lower()
    action_slug = (action or "").strip()

    # A skill supplies all three of the things a model would otherwise invent:
    # the action, a name in the operator's language, and the rule for when to
    # act. It wins over anything passed alongside it, because the point of
    # authoring them is that they are not negotiable per call.
    if (skill or "").strip():
        written = app_skills.get(skill)
        if written is None:
            return {
                "error": (
                    f"There is no skill called {skill!r}. Call "
                    "list_app_actions and use a `skill` from it, or pass a "
                    "raw `action` instead."
                )
            }
        if slug and slug != written.app:
            return {
                "error": (
                    f"Skill {written.slug!r} acts on {written.app}, not "
                    f"{slug}. Attach it to the right app or pick another."
                )
            }
        slug = written.app
        action_slug = written.action
        name = name.strip() or written.name
        # The authored rule is appended rather than replacing what the model
        # wrote: the model may know something about *this* business that the
        # skill cannot, and losing it would make the skill worse than the
        # freehand description it replaced.
        extra = (description or "").strip()
        description = f"{written.use_when}\n\n{extra}" if extra else written.use_when

    if not slug or not action_slug:
        return {
            "error": (
                "Both app and action are required. Call list_app_actions and "
                "use a slug from it."
            )
        }
    if not (name or "").strip():
        return {
            "error": (
                "Give the tool a name the agent will read, e.g. 'Email the "
                "confirmation'."
            )
        }

    # Scoped, deliberately not `get_workflow_by_id`: a workflow_id a model
    # produced is a request-supplied id however it came by it.
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        return {"error": f"No agent {workflow_id} in this account."}

    definition = dict(getattr(workflow, "workflow_definition", None) or {})
    if not definition.get("nodes"):
        return {
            "error": (
                f"Agent {workflow_id} has no nodes to attach a tool to. Open "
                f"it in the editor instead: /workflow/{workflow_id}"
            )
        }

    connected = {t.lower() for t in (await connected_toolkits(organization_id))}
    if slug not in connected:
        return {
            "error": (
                f"{slug} is not connected for this account. Offer connect_app "
                "first, then attach the tool once they have authorized it."
            )
        }

    # The action has to exist. An invented slug is accepted by every layer
    # below this one and fails at call time, mid-conversation.
    # A skill's action was authored and shape-checked at import, so it needs
    # no catalogue round trip -- and must not be blocked by one failing.
    actions = None if (skill or "").strip() else await composio_toolkit_actions(slug)
    if actions is None and not (skill or "").strip():
        return {
            "error": (
                f"Could not check {action_slug} against {slug} just now. Do "
                "not attach an unverified action -- say you will try again."
            )
        }
    known = {a["slug"] for a in (actions or [])} or {action_slug}
    if action_slug not in known:
        return {
            "error": (
                f"{slug} has no action called {action_slug}. Call "
                "list_app_actions and use one of the slugs it returns."
            )
        }

    # Which account of that app, when the organization has more than one --
    # three doctors' calendars under one identity. Checked against this
    # organization's own accounts rather than trusted: an id from elsewhere
    # would be a tool pointed at somebody else's calendar, and Composio
    # refusing it later is a failure on a live call rather than here.
    account_id = (connected_account_id or "").strip()
    account_label: Optional[str] = None
    if account_id:
        mine = {
            a["connected_account_id"]: a
            for a in (await composio_connected_accounts(organization_id))
            if (a.get("app") or "") == slug
        }
        if account_id not in mine:
            return {
                "error": (
                    f"{account_id} is not a {slug} account on this account. "
                    "Call list_app_accounts and use an id from it."
                )
            }
        account_label = mine[account_id].get("label")

    # The real user rather than a stand-in object. `create_tool_for_user`
    # reads `selected_organization_id` off it to scope the row, and a fake
    # carrying an org id we passed in would be us asserting the scoping
    # instead of it being checked.
    actor = await db_client.get_user_by_id(user_id)
    if actor is None or actor.selected_organization_id != organization_id:
        return {
            "error": (
                "This chat's account could not be confirmed, so no tool was "
                "created. Attach it from the editor instead."
            )
        }

    try:
        created = await create_tool_for_user(
            CreateToolRequest(
                name=name.strip()[:255],
                description=(description or "").strip() or None,
                definition={
                    "type": "composio",
                    "config": {
                        "toolkit": slug,
                        "tool_slug": action_slug,
                        **({"connected_account_id": account_id} if account_id else {}),
                    },
                },
            ),
            actor,
            source="agent_builder",
        )
    except Exception as exc:  # noqa: BLE001 - reported, never raised at a chat
        logger.exception("Could not create a Composio tool for org %s", organization_id)
        return {"error": f"Could not create the tool: {exc}"}

    tool_uuid = getattr(created, "tool_uuid", None) or getattr(created, "uuid", None)
    if not tool_uuid:
        return {
            "error": (
                "The tool was created but returned no id, so it could not be "
                "attached. Attach it from the editor instead."
            )
        }

    attached = _attach_to_nodes(definition, str(tool_uuid))
    if not attached:
        return {
            "error": (
                f"The tool exists but agent {workflow_id} has no node that can "
                f"hold one. Attach it from the editor: /workflow/{workflow_id}"
            )
        }

    await db_client.update_workflow(
        workflow_id=workflow_id,
        name=None,
        workflow_definition=definition,
        template_context_variables=None,
        workflow_configurations=None,
        organization_id=organization_id,
    )

    return {
        "attached": True,
        "workflow_id": workflow_id,
        "tool_uuid": str(tool_uuid),
        "app": slug,
        "action": action_slug,
        "skill": (skill or "").strip() or None,
        "acts_on": account_label,
        "published": False,
        "open_url": f"/workflow/{workflow_id}",
        "next_steps": [
            f"Tell the user {name.strip()} is on the draft, not live yet.",
            "They publish it from the agent's page when they are happy.",
            "Suggest testing it first -- the action runs for real.",
        ],
    }
