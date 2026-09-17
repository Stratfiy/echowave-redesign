"""Whether a run has anybody watching it, and what that permits.

A bot's connected-app tools run through their handler and execute: no card,
no confirmation, nothing between the model choosing ``GMAIL_SEND_EMAIL`` and
the mail leaving. That is right on a call. Somebody asked for the booking
out loud, a second ago, and is still on the line when it happens -- the
supervision is the conversation.

A routine is the other case. It fires at eight in the morning because a
schedule said so, reads what it finds, and acts with nobody there. The same
send, from the same bot, with the same tools, is a different act.

So the line is not which bot, and not which tool: it is **whether this
particular run has a person in it**. A bot can answer the phone and send a
confirmation all afternoon, and stay read-only on its schedule, without
being two bots.

``routine_writes`` turns the second case on per bot, for the operator who
wants exactly that -- the nightly chaser that really is meant to send. Off
by default, because the cost of the default being wrong is asymmetric: a
routine that could not send is a message nobody got, and a routine that
should not have sent is a message somebody did.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from loguru import logger

#: The key inside ``workflow_configurations``, alongside ``notify_on`` and
#: ``channel``. Stored there for the same reason they are: no migration, and
#: nothing about telephony has to learn a new field.
CONFIG_KEY = "routine_writes"

#: The annotation the routine runner stamps on a run before any turn of it
#: executes. Its presence is what makes a run unattended -- read from the
#: run rather than inferred from the bot, because the bot is the same bot
#: either way.
RUN_ANNOTATION = "routine"


def briefing(instruction: str, *, writes_allowed: bool) -> str:
    """The routine's instruction, with the room described around it.

    A routine's instruction reaches the bot as a plain user message, so the
    bot answers it the way it answers anybody. Built holding Gmail and asked
    to summarise an inbox, one replied "should I fetch the last 24h now to
    show a sample summary?" -- a reasonable thing to say to a person, at
    eight in the morning, to nobody. Nothing answered, the turn ended, and
    the deliverable was a question.

    So the room is described: nobody is reading, nothing can answer, do the
    work and report it. And when writes are gated, say so here rather than
    let the model plan a send and meet a refusal mid-turn -- a tool that
    fails reads, to a model, like something worth trying again.
    """
    lines = [
        "This is a scheduled run. Nobody is reading this and nobody can "
        "answer a question, so do not ask one.",
        "Do the work now with the tools you have and report what you found. "
        "If you could not, say plainly what stopped you.",
    ]
    if not writes_allowed:
        lines.append(
            "You cannot send, create or change anything on this run. Report only."
        )
    task = (instruction or "").strip()
    if task:
        lines.append(f"The task:\n{task}")
    return "\n\n".join(lines)


def writes_allowed(configurations: Optional[Mapping[str, Any]]) -> bool:
    """Whether this bot may use a write tool on an unattended run.

    Off unless the operator said otherwise, and off for anything that is not
    plainly ``true``: a string, a number or a null in that key is somebody's
    half-finished edit, not consent to send mail at 8am.
    """
    if not isinstance(configurations, Mapping):
        return False
    return configurations.get(CONFIG_KEY) is True


def is_unattended(annotations: Optional[Mapping[str, Any]]) -> bool:
    """Whether this run is a routine firing rather than a conversation."""
    if not isinstance(annotations, Mapping):
        return False
    return bool(annotations.get(RUN_ANNOTATION))


async def run_is_unattended(run_id: Optional[int]) -> bool:
    """The same question, asked of a run by id.

    Never raises, and answers False when it cannot tell. That direction is
    deliberate and it is the unsafe one, so it is worth saying why: this
    gate exists over a tool the bot was given on purpose, and a database
    blip that silently stripped a bot's tools mid-call would break a live
    conversation to protect a routine that is not running. The failure is
    logged so it is not silent.
    """
    if not run_id:
        return False
    try:
        from api.db import db_client

        run = await db_client.get_workflow_run(run_id)
        return is_unattended(getattr(run, "annotations", None))
    except Exception as exc:  # noqa: BLE001 - a live call must not end here
        logger.warning("Could not tell whether run {} is unattended: {}", run_id, exc)
        return False


__all__ = [
    "CONFIG_KEY",
    "RUN_ANNOTATION",
    "briefing",
    "is_unattended",
    "run_is_unattended",
    "writes_allowed",
]
