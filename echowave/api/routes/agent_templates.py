"""Ready-made agents a new account can put on a number.

The same catalogue the authoring LLM reads through MCP, exposed to the UI so
the overview screen can show a new account something it can start from instead
of an empty workflow list. Both surfaces read one source, so a template cannot
say one thing in the chat and another on the screen.

Read-only and static — the catalogue is code, not per-tenant data — so these
routes take a signed-in user for consistency with the rest of the API but hold
no organization-scoped state.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.db.models import UserModel
from api.services.agent_templates import AgentTemplate, get_template, list_templates
from api.services.auth.depends import get_user

router = APIRouter(prefix="/agent-templates", tags=["agent-templates"])


def _summary(template: AgentTemplate) -> dict[str, Any]:
    """What a picker card needs. Prompt bodies are fetched one at a time."""
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


@router.get("")
async def list_agent_templates(
    _user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Every template, in catalogue order."""
    return {"templates": [_summary(t) for t in list_templates()]}


@router.get("/{template_id}")
async def get_agent_template(
    template_id: str,
    _user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """One template in full, including prompts, guardrails and stack."""
    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
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
    }
