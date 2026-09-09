"""Ready-made agents a new account can put on a number.

The same catalogue the authoring LLM reads through MCP, exposed to the UI so
the overview screen can show a new account something it can start from instead
of an empty workflow list. Both surfaces read one source, so a template cannot
say one thing in the chat and another on the screen.

Read-only and static — the catalogue is code, not per-tenant data — so these
routes take a signed-in user for consistency with the rest of the API but hold
no organization-scoped state.
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException

from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.agent_templates import AgentTemplate, get_template, list_templates
from api.services.agent_templates.materialise import (
    TemplateShapeError,
    to_workflow_definition,
)
from api.services.auth.depends import get_user
from api.services.configuration.registry import ServiceProviders
from api.services.workflow.trigger_paths import regenerate_trigger_uuids

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


class CreateFromTemplateRequest(BaseModel):
    """What the template grid asks before it builds the agent.

    Only the voice, and only its gender. Everything else a template needs it
    already carries, and the first question anybody asks after picking one is
    whether the agent sounds male or female.
    """

    voice_gender: Literal["male", "female"] | None = Field(
        default=None,
        description=(
            "Give the agent a male or female voice. Omit to inherit the "
            "organization's default."
        ),
    )


@router.post("/{template_id}/create")
async def create_from_template(
    template_id: str,
    request: CreateFromTemplateRequest | None = None,
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Make this template into an agent the account owns, and return it.

    The other door into the product. The wizard asks eleven questions and then
    runs a language model to write a flow, which is the right thing for a
    business we have no template for and the wrong thing for a dental clinic
    when a dental clinic template already exists.

    No model override is written. The recommended stack on a template names
    vendors, and the agent-level override speaks in managed tiers; translating
    one into the other here would pin a vendor that the tier is meant to be
    free to move. Inheriting the organization default gets the same Indic-first
    stack the template's rationale is describing, and keeps moving when we move
    it.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    try:
        definition = to_workflow_definition(template)
    except TemplateShapeError as exc:
        # A broken catalogue entry is our bug, not the caller's. 500 rather
        # than 400 so it shows up as one.
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    workflow = await db_client.create_workflow(
        name=template.name,
        workflow_definition=regenerate_trigger_uuids(definition),
        user_id=user.id,
        organization_id=user.selected_organization_id,
        # A gender, never a speaker. "anushka" is a Sarvam name, and writing one
        # here is exactly the vendor pin the paragraph above refuses to make for
        # models — see DECIBYL_GENDER_VOICES. The sentinel is resolved to a real
        # voice at pipeline build, against whichever vendor the tier is on that
        # day, so this keeps working when the tier moves.
        workflow_configurations=_voice_override(request),
    )

    return {"id": workflow.id, "name": workflow.name, "template_id": template.id}


def _voice_override(request: CreateFromTemplateRequest | None) -> dict | None:
    """The agent-level override carrying the chosen voice, or nothing.

    None rather than an empty override when no gender was asked for: the agent
    then inherits the organization's configuration whole, which is what every
    template did before this existed.
    """
    gender = request.voice_gender if request else None
    if not gender:
        return None
    return {"tts": {"provider": ServiceProviders.DECIBYL.value, "voice": gender}}
