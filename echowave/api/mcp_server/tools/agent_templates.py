"""MCP tools that hand the authoring LLM a working starting point.

Without these the model invents a flow and a prompt from the user's sentence,
which produces something plausible that has never met a caller. With them the
first version of an agent carries prompts written for Indian phone
conversations and a stack the rate card can price.

Two tools rather than one: `list_agent_templates` is cheap and returns enough
to choose from, `get_agent_template` returns the full prompts. Returning every
prompt in the list call would spend most of a context window on five templates
the user did not want.
"""

from __future__ import annotations

from typing import Any, Optional

from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.services.agent_templates import (
    AgentTemplate,
    find_templates,
    get_template,
    list_templates,
)


def _summary(template: AgentTemplate) -> dict[str, Any]:
    """The shape of a template in a picker — no prompt bodies."""
    return {
        "id": template.id,
        "name": template.name,
        "vertical": template.vertical,
        "direction": template.direction.value,
        "summary": template.summary,
        "languages": template.languages,
        "example_requests": template.example_requests,
        "typical_call_seconds": template.call_shape.typical_call_seconds,
        "typical_minutes_per_month": template.call_shape.minutes_per_month,
    }


@traced_tool
async def list_agent_templates(query: Optional[str] = None) -> dict[str, Any]:
    """List ready-made agent templates for Indian verticals.

    Call this FIRST whenever a user asks for an agent for a recognisable kind of
    business — a clinic, a restaurant, property leads, loan reminders, course
    enquiries, COD orders. A template carries prompts written for Indian phone
    conversations and a provider stack the rate card can price, which is the
    part that is hard to get right from a one-line request.

    If a template fits, fetch it with `get_agent_template` and build from it
    rather than composing a flow from scratch. If none fits, say so and author
    normally — a template forced onto the wrong vertical is worse than none.

    Args:
        query: Optional free text describing the business or the goal, e.g.
            "dental clinic front desk" or "EMI reminder calls". Omit to get the
            whole catalogue; there are only a handful.

    Returns:
        `templates`: summaries with `id`, vertical, direction, languages and
        typical call shape. No prompt bodies — call `get_agent_template` for
        those. `matched` is false when a query returned nothing and the full
        catalogue is being shown instead.
    """
    await authenticate_mcp_request()

    if query:
        found = find_templates(query)
        if found:
            return {
                "templates": [_summary(t) for t in found],
                "matched": True,
            }
        return {
            "templates": [_summary(t) for t in list_templates()],
            "matched": False,
            "note": (
                "Nothing matched that description; showing the whole catalogue. "
                "If none of these fit the user's business, author the workflow "
                "normally rather than bending a template to fit."
            ),
        }

    return {"templates": [_summary(t) for t in list_templates()], "matched": True}


@traced_tool
async def get_agent_template(template_id: str) -> dict[str, Any]:
    """Fetch one template in full: stack, nodes, prompts, edges and guardrails.

    The `nodes` carry prompts meant to go into the workflow **unedited**. Treat
    them as the starting text and change only what the user has actually told
    you — swapping in their business details through `template_variables`, or
    applying a change they asked for. Rewriting a prompt for style loses the
    turn-taking, readback and refusal behaviour it was written to carry.

    `guardrails` are not style notes. Several are legal constraints for the
    vertical — what a loan reminder may not say, what a clinic agent may not
    advise — and they must survive into the deployed prompts. Carry them into
    a `globalNode` so they apply on every turn, rather than into one node.

    `template_variables` are values only the operator has. Ask for them, or
    leave them as `{{name}}` placeholders so an unfilled one is visible in
    testing rather than spoken to a caller.

    After building, call `estimate_agent_cost` with the template's stack so the
    user sees what the agent costs per minute before it dials anyone.

    Args:
        template_id: An `id` from `list_agent_templates`.

    Returns:
        The full template. Error codes: `template_not_found` when the id is
        unknown — re-list rather than guessing another id.
    """
    await authenticate_mcp_request()

    template = get_template(template_id)
    if template is None:
        return {
            "found": False,
            "error_code": "template_not_found",
            "error": (
                f"No template with id {template_id!r}. Call list_agent_templates "
                "for current ids."
            ),
        }

    return {
        "found": True,
        "template": {
            **_summary(template),
            "stack": template.stack.model_dump(),
            "call_shape": {
                "typical_call_seconds": template.call_shape.typical_call_seconds,
                "typical_calls_per_month": template.call_shape.typical_calls_per_month,
                "minutes_per_month": template.call_shape.minutes_per_month,
            },
            "nodes": [node.model_dump() for node in template.nodes],
            "edges": [edge.model_dump() for edge in template.edges],
            "guardrails": template.guardrails,
            "compliance_notes": template.compliance_notes,
            "template_variables": template.template_variables,
        },
        "next_steps": [
            "Ask the user for the template_variables you do not have.",
            "Put the guardrails in a globalNode so they apply on every turn.",
            "Build with create_workflow, then call estimate_agent_cost with the "
            "template's stack and tell the user the per-minute price.",
            "Numbers: call list_phone_numbers to offer one they already own. If "
            "they have none, use search_available_numbers to show options — but "
            "buying is done by the user in the Telephony screen, not by you.",
        ],
    }
