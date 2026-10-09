"""The provider card: how a workspace starts making images, in the thread.

Somebody asks for a Diwali poster and no image provider is ready. The answer
is not "go to Settings": it is this card, on the thread that asked, with the
three providers, a key field for the one picked, and a Connect button
(``PUT /images/provider``). Once connected, the card sends the request on
again by itself, so the person never retypes it.

The same card comes back when a key the workspace connected is refused by
its vendor, saying so, because that is where the key is fixed.

Nothing is connected by the card appearing. Saving a key is an admin's, as
with every other key in the vault; anybody else is told so on the card.
"""

from __future__ import annotations

from typing import Any

from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline

KIND = AgentEventKind.IMAGE_PROVIDER_OFFERED.value


async def offer(
    *,
    organization_id: int,
    request: str = "",
    reason: str = "",
    provider: str | None = None,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Put the card on the thread. Returns what the model is told."""
    await agent_timeline.record(
        organization_id=organization_id,
        kind=KIND,
        actor=AgentEventActor.AGENT.value,
        summary="Choose how to make images",
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        payload={
            # What to send on once connected, so nobody retypes it.
            "request": (request or "")[:500],
            "reason": (reason or "")[:300],
            "provider": provider,
            "offered_to": user_id,
        },
        in_channel=False,
    )
    if reason:
        return {
            "status": "needs_provider",
            "note": (
                f"{reason} A card to fix it is on the thread. Say that in one "
                "line and end your reply; do not tell anybody to go to "
                "Settings."
            ),
        }
    return {
        "status": "needs_provider",
        "note": (
            "No image provider is connected yet. A card is on the thread to "
            "choose Google Gemini, OpenAI or Amazon Bedrock and connect a key; "
            "once connected it sends this request on again by itself. Say the "
            "card is there in one line and end your reply. Do not tell anybody "
            "to go to Settings, and do not say the image was made."
        ),
    }
