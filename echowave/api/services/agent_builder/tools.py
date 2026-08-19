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
from api.services.billing.estimator import estimate_cost_per_minute
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph


def tool_schemas() -> list[dict[str, Any]]:
    """The catalogue, in OpenAI's function shape.

    That shape is the one all three vendors can be derived from without loss,
    so it is the form the adapters in `client.py` translate *from* rather than
    a fourth dialect to keep in sync.
    """
    return [
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
    ]


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
        "typical_call_seconds": template.call_shape.typical_call_seconds,
        "typical_calls_per_month": template.call_shape.typical_calls_per_month,
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

    stack = template.stack
    estimate = await estimate_cost_per_minute(
        session,
        organization_id=organization_id,
        stt_provider=stack.stt_provider,
        stt_model=stack.stt_model,
        llm_provider=stack.llm_provider,
        llm_model=stack.llm_model,
        tts_provider=stack.tts_provider,
        tts_model=stack.tts_model,
        telephony_provider=stack.telephony_provider,
    )

    per_minute = estimate.total_paise_per_minute / 100
    calls = calls_per_month or template.call_shape.typical_calls_per_month
    minutes = round(calls * template.call_shape.typical_call_seconds / 60)

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
