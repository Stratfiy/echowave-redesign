"""What a hired template is given beyond its prompts (OP-4).

A template used to be prompts and edges; the tools were the operator's to
add by hand. The Prospecting agent is nothing without a search and a
mailbox, so a template now says what it needs and the hire gives it:
the workspace's web tool (made if there is none), the tools of the
connected apps it names, matched the way a brief's named apps are, and
the send-approval setting that makes each email a card.

An app the workspace has not connected attaches nothing, and the agent's
readiness checklist already says which app is missing; nobody is sent
to a screen from here. Never raises past the definition: an agent built
without a tool it could not be given is the agent the operator would
have had, and losing the hire over a tool list is the worse trade.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.services.agent_templates._base import AgentTemplate
from api.services.workflow import agent_web, brief_apps, connected_tools, send_approval


async def for_template(
    definition: dict[str, Any],
    *,
    template: AgentTemplate,
    organization_id: int,
    user_id: int,
) -> dict[str, Any]:
    """The definition with the template's tools on its calling nodes."""
    uuids: list[str] = []
    if template.needs_web:
        try:
            uuid = await agent_web.ensure_tool(
                organization_id=organization_id, user_id=user_id
            )
            if uuid:
                uuids.append(uuid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not give {} the web: {}", template.id, exc)
    if template.apps:
        try:
            tools = list(await connected_tools.list_for_organization(organization_id))
            tools += await connected_tools.mcp_for_organization(organization_id)
            uuids += brief_apps.tool_uuids(" ".join(template.apps), tools)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not give {} its apps: {}", template.id, exc)
    if uuids:
        logger.info("Hired {} with {} tool(s)", template.id, len(uuids))
    return brief_apps.attach(definition, uuids)


def configurations(
    base: dict[str, Any] | None, *, template: AgentTemplate
) -> dict[str, Any] | None:
    """The hire's configurations with the template's settings on them."""
    if not template.approve_sends:
        return base
    return {**(base or {}), send_approval.CONFIG_KEY: True}


__all__ = ["configurations", "for_template"]
