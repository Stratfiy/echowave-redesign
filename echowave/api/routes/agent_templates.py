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
from loguru import logger

from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import PostHogEvent
from api.services.agent_templates import AgentTemplate, get_template, list_templates
from api.services.agent_templates.materialise import (
    TemplateShapeError,
    to_workflow_definition,
)
from api.services.auth.depends import get_user
from api.services.posthog_client import capture_event
from api.services.configuration.agent_options import managed_stack_override
from api.services.configuration.ai_model_configuration import (
    get_organization_ai_model_configuration_v2,
)
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
        workflow_configurations=await _voice_override(
            request, organization_id=user.selected_organization_id
        ),
    )

    # The third door into an agent, and the only one that was silent. The
    # wizard and the blank canvas both report themselves, so a funnel built on
    # workflow_created undercounted exactly the path we are steering people to.
    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.WORKFLOW_CREATED,
        properties={
            "workflow_id": workflow.id,
            "workflow_name": workflow.name,
            "source": "template_grid",
            "template_id": template.id,
            "vertical": template.vertical,
            # Whether anybody uses the voice choice at all, which is the only
            # way to find out whether it was worth asking.
            "voice_gender": request.voice_gender if request else None,
            "organization_id": user.selected_organization_id,
        },
    )

    return {"id": workflow.id, "name": workflow.name, "template_id": template.id}


async def _voice_override(
    request: CreateFromTemplateRequest | None, *, organization_id: int
) -> dict | None:
    """The agent-level override carrying the chosen voice, or nothing.

    None rather than an empty override when no gender was asked for: the agent
    then inherits the organization's configuration whole, which is what every
    template did before this existed.

    An agent-level override is a *whole* stack — there is no way to say "the
    account's setup but a different voice", because every slot is compiled
    together. So the account's own tiers are read and carried forward. Writing
    the defaults instead would quietly move an account that had chosen the
    accurate brain back down to the standard one, every time somebody started
    from a template, and nothing would have said so.

    A BYOK account gets no override at all. Its slots name real vendors and
    real keys, and a managed stack written over the top would take the agent
    off the customer's own models — a far larger change than the voice they
    asked for.
    """
    gender = request.voice_gender if request else None
    if not gender:
        return None

    configuration = await get_organization_ai_model_configuration_v2(organization_id)
    managed = getattr(configuration, "decibyl", None) if configuration else None
    if managed is None:
        logger.info(
            "Template voice not applied for org {}: the account is not on a "
            "managed stack, so an override would replace its own models.",
            organization_id,
        )
        return None

    if (managed.realtime_tier or "").strip():
        # A speech-to-speech account has no voice slot to write into — one
        # model hears and speaks, and managed_stack_override emits no tts
        # section at all for it. Writing the override anyway would record a
        # choice that is then silently discarded, which is worse than not
        # recording it: the customer would see a voice they asked for on the
        # agent and hear a different one on the call.
        logger.info(
            "Template voice not applied for org {}: the account is on a "
            "speech-to-speech bundle, where the model provides the voice.",
            organization_id,
        )
        return None

    override = managed_stack_override(
        voice=gender,
        llm_tier=managed.llm_tier or "default",
        stt_tier=managed.stt_tier or "default",
        tts_tier=managed.tts_tier or "default",
    )
    return override or None
