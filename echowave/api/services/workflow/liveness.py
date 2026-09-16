"""Whether an agent is taking calls, asked in one place.

An operator flips a toggle and expects the phone to stop being answered. That
promise is only as good as its least-careful call path, so all four of them ask
the same question here:

* the inbound webhook dispatcher (``routes/telephony.handle_inbound_run``)
* inbound over ARI (``services/telephony/ari_manager``)
* the outbound API (``routes/telephony.initiate_call``)
* the campaign dispatcher

A flag honoured by three of those is worse than no flag at all: the operator
believes the agent is off, and it answers anyway.

Two conditions, not one. An **archived** agent must not take calls either —
that was true before this module existed and nothing enforced it, so a number
still pointed at an archived agent kept being answered by it. Archiving is a
stronger statement than pausing, and it would be strange for the weaker one to
be the only one with teeth.

Three now. A **chat** bot has no voice configuration and a graph written for
somebody reading rather than listening; handed a call it would answer in a
default voice with prompts about messages. That is not a pause and not an
archive — the bot is working, on the channel it was built for — so it refuses
with its own reason rather than borrowing one that would send an operator to
look for a toggle that is already on.
"""

from __future__ import annotations

from api.enums import WorkflowStatus
from api.schemas.workflow_configurations import is_chat


class AgentNotTakingCalls(Exception):
    """This agent exists and is deliberately not answering.

    A refusal the caller is expected to surface, not an error to retry — the
    same shape as ``dnd.CallRefused``. Retrying changes nothing until somebody
    flips the toggle back.
    """

    #: Short, stable token stored against a refused campaign row and returned
    #: to the API caller. The human sentence changes; this does not.
    reason: str = "agent_not_live"

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        if reason:
            self.reason = reason


def workflow_is_live(workflow) -> bool:
    """Whether this agent should be handed a call.

    Tolerant of a workflow object that predates the column — ``getattr`` with a
    default of True — because the alternative is that a stale row or a partial
    mock silently stops answering. Absence of the flag is not a decision to
    pause.
    """
    if getattr(workflow, "status", WorkflowStatus.ACTIVE.value) != (
        WorkflowStatus.ACTIVE.value
    ):
        return False
    if is_chat(getattr(workflow, "workflow_configurations", None)):
        return False
    return bool(getattr(workflow, "is_live", True))


def assert_workflow_may_take_calls(workflow) -> None:
    """Raise :class:`AgentNotTakingCalls` unless this agent is answering.

    Takes the workflow rather than an id: every caller has already loaded it to
    check tenancy, and a second query here would be a second chance to load a
    different row.
    """
    if workflow is None:
        raise AgentNotTakingCalls("No agent is assigned.", reason="agent_missing")

    if getattr(workflow, "status", WorkflowStatus.ACTIVE.value) != (
        WorkflowStatus.ACTIVE.value
    ):
        raise AgentNotTakingCalls(
            f"Agent {getattr(workflow, 'id', '?')} is archived and does not "
            "take calls.",
            reason="agent_archived",
        )

    # Before the pause check, because a chat bot is not paused. Saying it is
    # would send somebody to the agent list to turn on a toggle that is
    # already on, and the real answer -- this bot does not do phones -- is
    # nowhere on that screen.
    if is_chat(getattr(workflow, "workflow_configurations", None)):
        raise AgentNotTakingCalls(
            f"Agent {getattr(workflow, 'id', '?')} answers in writing, not on "
            "the phone. Point this number at a voice bot, or change this one's "
            "channel in its settings.",
            reason="agent_is_chat_only",
        )

    if not bool(getattr(workflow, "is_live", True)):
        raise AgentNotTakingCalls(
            f"Agent {getattr(workflow, 'id', '?')} is paused and is not taking "
            "calls. Turn it on from the agent list to start answering again.",
        )
