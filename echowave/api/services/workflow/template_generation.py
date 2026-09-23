"""Produce a workflow definition from a natural-language template request.

The hosted Model Proxy Service (MPS) generates a bespoke workflow from a
use-case description. A self-hosted deployment has no MPS — the hostname is
either unset, unreachable, or pointed at a service the operator does not run —
and until now that turned "Create Voice Agent" into a 500 with a DNS error in
the body.

A first-run user who cannot create an agent has no way to discover that the
rest of the product works. So when MPS is unavailable this module builds a
starter workflow locally: a four-node agent, wired and valid, with the user's
own words in the prompts. It is not as good as a generated one. It opens in the
editor and it answers the phone, which is the whole point.

The builder is pure and the resolver is the only thing that touches the
network, so the shape of what gets created is testable without a socket.
"""

from typing import Any, Optional

import httpx
from loguru import logger

from api.constants import DECIBYL_MPS_SECRET_KEY, DEPLOYMENT_MODE
from api.enums import BotChannel
from api.services.mps_service_key_client import mps_service_key_client
from api.services.workflow.qa_node import qa_node

# MPS statuses that mean "this service is not really there", as opposed to
# "you called it wrong". A 404 is a host answering that has never heard of the
# workflow API; 5xx is the service itself failing. Both are better served by a
# local starter than by an error page. Anything else — 401, 403, 422 — is a
# configured MPS rejecting a specific request, and hiding that behind a
# fallback would leave the operator debugging a wrong secret key by guesswork.
_ABSENT_STATUSES = frozenset({404, 501, 502, 503, 504})

# The persona applies to every prompted node. It is deliberately language-neutral:
# the platform supports eleven Indian languages plus auto-detect, and hardcoding
# one here would quietly undo that for every agent created this way.
GLOBAL_PROMPT = (
    "You are a helpful, polite assistant speaking on the phone. Reply in the "
    "same language the other person is speaking, and switch if they switch. "
    "Use short conversational sentences and simple words. Never use characters "
    "that cannot be spoken aloud, such as bullet points, asterisks or emoji. "
    "Read numbers out digit by digit."
)


def _clean(text: str, fallback: str) -> str:
    stripped = (text or "").strip()
    return stripped or fallback


def build_starter_workflow(
    call_type: str,
    use_case: str,
    activity_description: str,
    channel: BotChannel = BotChannel.VOICE,
) -> dict[str, Any]:
    """Build a valid four-node workflow from the template request.

    Returns the same shape MPS returns, so callers cannot tell the difference:
    ``{"name": str, "workflow_definition": {"nodes": [...], "edges": [...]}}``.

    Args:
        call_type: ``INBOUND`` or ``OUTBOUND``. Anything else is treated as
            inbound, because an unrecognised value should still yield a working
            agent rather than an exception on the create path. Ignored for a
            chat bot, which has no direction.
        use_case: Short label for what the agent is for.
        activity_description: The user's own description of the conversation.
        channel: Voice or chat. A chat bot runs the same graph -- the text
            runner builds a ``WorkflowGraph`` exactly as the pipeline does --
            so the difference here is entirely in what the bot is told and
            what it is told about. "Thank you for calling" in a chat window
            is the sort of thing a customer notices and we do not, and the
            voice-only flags below (interruption, spoken greeting) describe
            physics that writing does not have.
    """
    is_chat = channel is BotChannel.CHAT
    is_outbound = not is_chat and (call_type or "").strip().upper() == "OUTBOUND"
    use_case = _clean(use_case, "Chat Agent" if is_chat else "Voice Agent")
    activity = _clean(
        activity_description,
        "Have a helpful conversation."
        if is_chat
        else "Have a helpful conversation with the caller.",
    )

    if is_chat:
        greeting = "Hi — what can I help you with?"
        start_prompt = (
            "Open briefly and find out what they need. Keep it to a line or "
            "two — they are reading, not listening — then move on."
        )
    elif is_outbound:
        greeting = (
            f"Hello, this is an assistant calling about {use_case}. "
            "Is now a good time to talk?"
        )
        start_prompt = (
            "Introduce yourself and say why you are calling. Confirm you are "
            "speaking to the right person and that they have a moment, then "
            "move on. If they say it is a bad time, offer to call back later "
            "and end the call politely."
        )
    else:
        greeting = "Hello, thank you for calling. How can I help you today?"
        start_prompt = (
            "Greet the caller warmly and find out what they need. Keep this "
            "step short — one or two turns — then move on."
        )

    nodes: list[dict[str, Any]] = [
        {
            "id": "global-1",
            "type": "globalNode",
            "position": {"x": -320, "y": 0},
            "data": {"name": "Persona", "prompt": GLOBAL_PROMPT},
        },
        {
            "id": "start-1",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Start" if is_chat else "Start Call",
                "is_start": True,
                "greeting": greeting,
                "greeting_type": "text",
                "prompt": start_prompt,
                # Interruption is a fact about speech. In writing a message is
                # sent whole, so a bot told it may be cut off mid-sentence is
                # being told about physics it does not have.
                "allow_interrupt": not is_chat,
                "add_global_prompt": True,
            },
        },
        {
            "id": "agent-1",
            "type": "agentNode",
            "position": {"x": 0, "y": 220},
            "data": {
                "name": use_case[:60],
                "prompt": activity,
                "allow_interrupt": True,
                "add_global_prompt": True,
            },
        },
        {
            "id": "end-1",
            "type": "endCall",
            "position": {"x": 0, "y": 440},
            "data": {
                "name": "End" if is_chat else "End Call",
                "is_end": True,
                "prompt": (
                    "Summarise what was agreed in one sentence and close politely."
                    if is_chat
                    else "Summarise what was agreed in one sentence, thank "
                    "them for their time, and say goodbye."
                ),
                "add_global_prompt": True,
            },
        },
        # Review is on from the start, like every other creation path. Sitting
        # in the same list rather than appended afterwards because there is
        # nothing conditional about it: this builder writes the whole graph,
        # so there is no earlier node set that could already carry one.
        qa_node(),
    ]

    edges: list[dict[str, Any]] = [
        {
            "id": "start-1-agent-1",
            "source": "start-1",
            "target": "agent-1",
            "data": {
                "label": "continue",
                "condition": (
                    "They have replied and it is clear what they want."
                    if is_chat
                    else "The caller has responded and it is clear what they want."
                ),
            },
        },
        {
            "id": "agent-1-end-1",
            "source": "agent-1",
            "target": "end-1",
            "data": {
                "label": "finished",
                "condition": (
                    "The conversation is complete, or they want to stop."
                    if is_chat
                    else "The conversation is complete, or the caller wants to hang up."
                ),
            },
        },
    ]

    return {
        "name": (
            f"{use_case} - Chat"
            if is_chat
            else f"{use_case} - {'Outbound' if is_outbound else 'Inbound'}"
        ),
        "workflow_definition": {"nodes": nodes, "edges": edges},
    }


def mps_is_configured() -> bool:
    """Whether this deployment has an MPS it can legitimately call.

    OSS mode authenticates with a per-user header rather than a shared secret,
    so it is always considered configured; the transport check below is what
    catches an absent host there.
    """
    if DEPLOYMENT_MODE == "oss":
        return True
    return bool(DECIBYL_MPS_SECRET_KEY)


async def generate_workflow_definition(
    call_type: str,
    use_case: str,
    activity_description: str,
    organization_id: Optional[int] = None,
    created_by: Optional[str] = None,
    channel: BotChannel = BotChannel.VOICE,
) -> dict[str, Any]:
    """Return a workflow definition, from MPS when it is available.

    Falls back to :func:`build_starter_workflow` when MPS is unconfigured or
    unreachable. Raises :class:`httpx.HTTPStatusError` when a configured MPS
    rejects the request, so a wrong secret key surfaces instead of silently
    degrading every agent anyone creates.

    A chat bot is always built locally. MPS takes a call type and returns a
    call: asking it for a chat bot and hoping would produce a graph that
    greets people for ringing, and there is no field in that request to say
    otherwise. Better a starter graph that is right about the channel than a
    generated one that is wrong about it -- and this is the local builder MPS
    itself falls back to, not a lesser path.
    """
    if channel is BotChannel.CHAT:
        logger.info("Chat agent requested; MPS returns call graphs, building locally.")
        return build_starter_workflow(
            call_type, use_case, activity_description, channel=channel
        )

    if not mps_is_configured():
        logger.info(
            "MPS is not configured (no DECIBYL_MPS_SECRET_KEY); "
            "creating a starter workflow locally."
        )
        return build_starter_workflow(
            call_type, use_case, activity_description, channel=channel
        )

    try:
        return await mps_service_key_client.call_workflow_api(
            call_type=call_type,
            use_case=use_case,
            activity_description=activity_description,
            organization_id=organization_id,
            created_by=created_by,
        )
    except httpx.TransportError as e:
        # DNS failure, refused connection, timeout. The service is not there.
        logger.warning(
            f"MPS unreachable ({e.__class__.__name__}: {e}); "
            "creating a starter workflow locally."
        )
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else None
        if status not in _ABSENT_STATUSES:
            raise
        logger.warning(f"MPS returned {status}; creating a starter workflow locally.")

    return build_starter_workflow(
        call_type, use_case, activity_description, channel=channel
    )
