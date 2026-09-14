"""A bot stops to ask a person, and the person answers on the card.

The Team screen had a "needs attention" tone and nothing behind it: a bot
that could not decide something wrote a line and carried on, or wrote a line
and stopped, and either way the person read a sentence with nothing to press.
This is the thing to press.

Two halves, deliberately in one module so they cannot drift:

``ask`` is what the bot calls -- a tool, registered on text and channel runs
only (a caller on the phone cannot wait for the clinic owner to open the
app). It writes a ``needs_decision`` row whose payload carries the question,
why it is being asked, the options, and how many may be picked.

``decide`` is what the person's click does. It writes the answer INTO THE SAME
ROW (``payload["decided"]``), so the card that asked is the card that shows
the answer to everyone who opens the channel afterwards, then posts the answer
as a message in the channel and hands it to the bot, which resumes with it in
front of it exactly the way it sees any other channel message.

Modes: ``single`` (one of the options), ``multi`` (any number), ``approve``
(Approve / Reject, no options of the bot's own). ``allow_other`` lets the
person type something the bot did not offer -- the "Other" the reference
screens have, because a fixed list is a form, not a conversation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline

TOOL_NAME = "ask_for_decision"
DESCRIPTION = (
    "Stop and ask a person on the team to choose, when you cannot or should "
    "not decide alone: spending money, cancelling something, anything the "
    "business would want to be asked about. State the question in one line, "
    "say why in one line, and offer the options. You will not get an answer "
    "in this turn; say that you have asked and end your reply."
)

MODES = ("single", "multi", "approve")
APPROVE_OPTIONS = ["Approve", "Reject"]
MAX_OPTIONS = 8
MAX_OPTION_CHARS = 120
MAX_QUESTION_CHARS = 300


def tool_properties() -> dict[str, Any]:
    return {
        "question": {
            "type": "string",
            "description": "The one-line question a person has to answer.",
        },
        "why": {
            "type": "string",
            "description": "One line on why you are asking rather than deciding.",
        },
        "options": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "The choices, two to eight, each a short phrase. Leave empty "
                "for mode 'approve'."
            ),
        },
        "mode": {
            "type": "string",
            "enum": list(MODES),
            "description": (
                "'single' for one choice, 'multi' for any number, 'approve' "
                "for a yes/no on something you propose."
            ),
        },
        "allow_other": {
            "type": "boolean",
            "description": "Whether the person may type an answer you did not offer.",
        },
    }


def normalise(arguments: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The payload a card can render, or None when the ask is not answerable.

    Strict on purpose: a card with no question or no options is a card with
    nothing to press, which is the failure this exists to end.
    """
    question = str(arguments.get("question") or "").strip()[:MAX_QUESTION_CHARS]
    if not question:
        return None
    mode = arguments.get("mode") if arguments.get("mode") in MODES else "single"
    raw = arguments.get("options") or []
    options: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        text = str(item or "").strip()[:MAX_OPTION_CHARS]
        if text and text not in options:
            options.append(text)
    if mode == "approve":
        options = list(APPROVE_OPTIONS)
    options = options[:MAX_OPTIONS]
    if len(options) < 2:
        return None
    return {
        "question": question,
        "why": str(arguments.get("why") or "").strip()[:MAX_QUESTION_CHARS],
        "options": options,
        "mode": mode,
        "allow_other": bool(arguments.get("allow_other")) and mode != "approve",
    }


async def ask(
    *,
    organization_id: Optional[int],
    workflow_id: Optional[int],
    workflow_run_id: Optional[int],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Record the question. Returns what the model is told."""
    payload = normalise(arguments)
    if payload is None:
        return {
            "status": "not_asked",
            "reason": "A question and at least two options are needed.",
        }
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.NEEDS_DECISION.value,
        summary=payload["question"],
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        payload=payload,
    )
    return {
        "status": "asked",
        "note": (
            "A person on the team has been asked and will answer in this "
            "channel. Tell them you have asked, then end your reply."
        ),
    }


class DecisionError(ValueError):
    """The answer cannot be recorded; the message says why, for the screen."""


async def decide(
    *,
    organization_id: int,
    event_id: int,
    choice: list[str],
    other: Optional[str],
    user_id: int,
) -> dict[str, Any]:
    """Write the answer onto the card and hand it to the bot.

    Returns the updated payload. Raises DecisionError for the screen to show.
    """
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if event is None or event.kind != AgentEventKind.NEEDS_DECISION.value:
        raise DecisionError("That question is not here to answer.")
    payload = dict(event.payload or {})
    if payload.get("decided"):
        # Not an error to retry with the same answer, and not a way to change
        # it either: the bot has already acted on the first one.
        raise DecisionError("Already answered.")

    options = list(payload.get("options") or [])
    mode = payload.get("mode") or "single"
    picked = [c for c in choice if c in options]
    typed = (other or "").strip()[:MAX_OPTION_CHARS]
    if typed and not payload.get("allow_other"):
        raise DecisionError("This question does not take a written answer.")
    if mode != "multi" and len(picked) > 1:
        raise DecisionError("Pick one.")
    if not picked and not typed:
        raise DecisionError("Pick an option.")

    decided = {
        "choice": picked,
        "other": typed or None,
        "by": user_id,
        "at": datetime.now(UTC).isoformat(),
    }
    payload["decided"] = decided
    if not await db_client.set_agent_event_payload(
        event_id, organization_id=organization_id, payload=payload
    ):
        raise DecisionError("That question is not here to answer.")

    answer = ", ".join(picked) if picked else typed
    if typed and picked:
        answer = f"{answer} -- {typed}"
    line = f'Decision on "{payload["question"]}": {answer}'

    # The answer is a message in the channel like any other, so the bot reads
    # it the way it reads everything else said there, and so the people in
    # the channel see who answered. Then the bot is asked to carry on.
    folder_id = event.folder_id
    if folder_id is None and event.workflow_id is not None:
        folder_id = await agent_timeline._folder_for(event.workflow_id, organization_id)
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary=line,
        workflow_id=event.workflow_id,
        folder_id=folder_id,
        payload={"body": line, "author_id": user_id, "decision_event_id": event_id},
    )
    if folder_id is not None and event.workflow_id is not None:
        try:
            from api.tasks.arq import enqueue_job
            from api.tasks.function_names import FunctionNames

            await enqueue_job(
                FunctionNames.ANSWER_CHANNEL_MESSAGE,
                event.workflow_id,
                folder_id,
                line,
            )
        except Exception as exc:  # noqa: BLE001 - the answer is recorded regardless
            logger.error(
                "Decision {} recorded but workflow {} could not be asked to continue: {}",
                event_id,
                event.workflow_id,
                exc,
            )
    return payload
