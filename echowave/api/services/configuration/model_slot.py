"""Point one slot of an agent's stack at a managed catalogue model.

The Advanced tiles' pencil and the voice row in the agent's About panel both
change one slot, and both must refuse what the catalogue does not sell and
what the stack cannot run. Kept here, out of the route, so the two cannot
drift: the pencil writes the result into the draft, the About panel puts it
live (``services/workflow/live_voice``).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from api.db import db_client
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
)


class SlotRefused(ValueError):
    """The slot cannot be set; the message says why, for the screen."""


async def ensure_sellable(*, component: str, provider: str, model: str) -> None:
    """Refuse anything that is not on the sellable catalogue for this slot."""
    from api.services.configuration import model_catalogue

    async with db_client.async_session() as session:
        offered = await model_catalogue.sellable(session, component=component)
    if not any(e.provider == provider and e.model == model for e in offered):
        raise SlotRefused(
            f"{provider} {model} is not on offer for this slot. "
            "Use the per-slot editor to run a model on your own key."
        )


async def _base_stack(existing: dict, *, organization_id: int) -> dict:
    """The stack these configurations run on today.

    The agent's own override if it has one, else what it inherits from the
    workspace -- so changing the voice never changes the brain.
    """
    from api.services.configuration.agent_options import stack_from_configurations
    from api.services.configuration.ai_model_configuration import (
        compile_workflow_model_configuration_override,
        get_resolved_ai_model_configuration,
    )
    from api.services.configuration.resolve import resolve_effective_config

    current_override = existing.get(WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY)
    if isinstance(current_override, dict) and isinstance(
        current_override.get("stack"), dict
    ):
        return dict(current_override["stack"])
    if current_override:
        return stack_from_configurations(
            compile_workflow_model_configuration_override(current_override)
        )
    resolved = await get_resolved_ai_model_configuration(
        organization_id=organization_id
    )
    return stack_from_configurations(
        resolve_effective_config(resolved.effective, existing.get("model_overrides"))
    )


async def with_slot(
    workflow_configurations: dict | None,
    *,
    organization_id: int,
    component: str,
    provider: str,
    model: str,
    voice: str | None = None,
    tuning: dict[str, Any] | None = None,
) -> tuple[dict, dict]:
    """These configurations with one slot changed, and that slot as it was.

    Pure apart from reading the workspace default; writes nothing. Raises
    ``SlotRefused`` for a stack that cannot run, so it is refused here
    rather than on the first call.
    """
    from api.services.configuration.agent_options import (
        SelectionError,
        with_model_slot,
    )
    from api.services.configuration.ai_model_configuration import (
        compile_workflow_model_configuration_override,
    )

    existing = dict(workflow_configurations or {})
    base = await _base_stack(existing, organization_id=organization_id)
    try:
        stack = with_model_slot(
            base,
            component=component,
            provider=provider,
            model=model,
            voice=voice,
            tuning=tuning,
        )
    except SelectionError as exc:
        raise SlotRefused(str(exc)) from exc
    try:
        compile_workflow_model_configuration_override({"version": 3, "stack": stack})
    except (ValueError, ValidationError) as exc:
        raise SlotRefused(str(exc)) from exc

    existing.pop("model_overrides", None)
    existing[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY] = {
        "version": 3,
        "stack": stack,
    }
    before = base.get(component) if isinstance(base, dict) else None
    return existing, before if isinstance(before, dict) else {}
