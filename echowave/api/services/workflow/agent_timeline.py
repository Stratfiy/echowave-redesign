"""Recording what happened, in a sentence a person can read.

The screen that needed this: a customer asked why their agent refused a free
noon slot, and answering it took an SSH session and a psql prompt. The facts
were all recorded -- across ``workflow_runs``, ``app_interactions`` and the
container's logs, at three different grains, joinable only by hand.

So every notable moment is written once, as a line, at the moment it happens.
Three rules hold this module together.

**The sentence is written here, not by a screen.** A summary assembled at
render time from a payload drifts the moment the payload shape changes, and
the history then reads differently than it did. For a record somebody may
rely on in a dispute -- "your agent told my patient we were closed" -- that is
the wrong property to have.

**It never raises into a call.** Same posture as
``services/workflow/app_interactions``: a failure to write a line must not end
a conversation. The cost is a hole in a history; the cost of the alternative is
a caller hearing the line go dead because a database was slow.

**A failure is an event.** ``COULD_NOT`` exists because the defect this log is
meant to end is the silent one -- the agent that was asked to confirm a booking
by phone, could not, and said nothing. A timeline that records only successes
is the same lie in a new place.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind, AgentEventVisibility

#: Kinds a person is *handed* rather than merely shown: they render as a card
#: in the thread and appear in the Deliverables list.
#:
#: An allowlist, which this codebase normally warns against -- but the failure
#: direction is the safe one here. Wrongly excluded means a card that renders
#: as a timeline line, which somebody notices. Wrongly *included* would put a
#: recording in a list somebody screenshots.
DELIVERABLE_KINDS = frozenset(
    {
        AgentEventKind.OUTCOME_FILED.value,
        AgentEventKind.DELIVERABLE.value,
        AgentEventKind.COULD_NOT.value,
        AgentEventKind.NEEDS_ATTENTION.value,
    }
)

#: Kinds that carry a caller's own words or identity, and are therefore hidden
#: until somebody asks. The caller was told what the recording was for; a
#: screen that shows it because nobody thought about it has broken that.
ON_REQUEST_KINDS = frozenset({AgentEventKind.CALLER_WANTED.value})


def default_visibility(kind: str) -> str:
    """Where a kind sits by default, before an organisation's own settings."""
    if kind in ON_REQUEST_KINDS:
        return AgentEventVisibility.ON_REQUEST.value
    return AgentEventVisibility.ALWAYS.value


async def record(
    *,
    organization_id: Optional[int],
    kind: str,
    summary: str,
    actor: str = AgentEventActor.AGENT.value,
    workflow_id: Optional[int] = None,
    definition_id: Optional[int] = None,
    workflow_run_id: Optional[int] = None,
    folder_id: Optional[int] = None,
    payload: Optional[dict[str, Any]] = None,
    visibility: Optional[str] = None,
    is_deliverable: Optional[bool] = None,
    in_channel: bool = True,
) -> None:
    """Write one line. Silent on failure, by design -- see the module docstring.

    ``in_channel=False`` keeps the row out of the bot's channel: a person
    talking to a bot on its own chat is not talking in the channel the bot
    happens to sit in, and the folder is left empty rather than resolved.

    ``folder_id`` is resolved from the bot when the caller does not know it,
    and stored rather than joined at read time: a bot moved to another team
    later must not rewrite its own history. The row records which team it was
    working for when it happened, which is the answer somebody actually wants.
    """
    if not organization_id:
        # Without a tenant the row cannot be read back by anyone who should
        # see it, and could be read by someone who should not. Dropping it is
        # the safe direction -- the same call app_interactions makes.
        logger.debug("Not recording agent event {}: no organization", kind)
        return

    if not (summary or "").strip():
        # A blank line on a timeline is worse than no line: it reads as a
        # broken screen rather than as nothing having happened.
        logger.warning("Not recording agent event {}: no summary", kind)
        return

    try:
        if in_channel and folder_id is None and workflow_id is not None:
            folder_id = await _folder_for(workflow_id, organization_id)

        await db_client.record_agent_event(
            organization_id=organization_id,
            kind=kind,
            actor=actor,
            summary=summary,
            workflow_id=workflow_id,
            definition_id=definition_id,
            workflow_run_id=workflow_run_id,
            folder_id=folder_id,
            payload=payload or {},
            is_deliverable=(
                kind in DELIVERABLE_KINDS if is_deliverable is None else is_deliverable
            ),
            visibility=visibility or default_visibility(kind),
        )
    except Exception as exc:  # noqa: BLE001 - a timeline must never end a call
        logger.warning("Could not record agent event {}: {}", kind, exc)


def call_summary(
    *, answered: bool, duration_seconds: int, disposition: Optional[str]
) -> str:
    """The one line a call gets in the thread: length, and how it ended."""
    minutes, seconds = divmod(max(int(duration_seconds or 0), 0), 60)
    length = f"{minutes}m{seconds:02d}s"
    line = f"Call · {length}" if answered else "Call not answered"
    tidy = (disposition or "").replace("_", " ").strip()
    return f"{line} · {tidy}" if tidy else line


async def record_call_ended(workflow_run_id: int) -> None:
    """One row per finished call, so a voice bot's thread is not empty.

    Every call this platform took left runs, recordings and costs, and not
    one line on the bot's own timeline: the thread showed messages and
    outcomes and nothing for the thing a phone bot spends its day doing. A
    bot with a hundred calls opened on "Nothing yet". Written at completion,
    after costing, for every run that was a call (text chat and channel
    replies write their own rows).
    """
    try:
        run = await db_client.get_workflow_run(workflow_run_id)
        if run is None or (run.mode or "") == "textchat":
            return
        organization_id = await db_client.get_organization_id_by_workflow_run_id(
            workflow_run_id
        )
        context = run.gathered_context or {}
        disposition = context.get("mapped_call_disposition") or context.get(
            "call_disposition"
        )
        # answered_at is stamped by the carrier's status webhook, which only
        # outbound calls have. An inbound call and a web test call are
        # answered by definition -- somebody was on the line -- and the
        # billable seconds say so. Judged on both, or a clinic's whole day of
        # inbound calls reads as missed.
        duration = run.billable_seconds
        if duration is None and run.answered_at and run.ended_at:
            duration = int((run.ended_at - run.answered_at).total_seconds())
        answered = run.answered_at is not None or (duration or 0) > 0
        await record(
            organization_id=organization_id,
            kind=AgentEventKind.CALL_ENDED.value,
            actor=AgentEventActor.AGENT.value,
            summary=call_summary(
                answered=answered,
                duration_seconds=duration or 0,
                disposition=str(disposition) if disposition else None,
            ),
            workflow_id=run.workflow_id,
            workflow_run_id=workflow_run_id,
            payload={
                "run_id": workflow_run_id,
                "duration_seconds": int(duration or 0),
                "answered": answered,
                "mode": run.mode,
                "call_type": str(getattr(run.call_type, "value", run.call_type) or ""),
                "disposition": str(disposition) if disposition else None,
                "has_recording": bool(run.recording_url),
            },
        )
    except Exception as exc:  # noqa: BLE001 - a timeline must never fail a completion
        logger.warning(
            "Could not record the call row for run {}: {}", workflow_run_id, exc
        )


async def _folder_for(workflow_id: int, organization_id: int) -> Optional[int]:
    """Which team this bot was working for, or None."""
    try:
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        return getattr(workflow, "folder_id", None) if workflow else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not resolve the team for workflow {}: {}", workflow_id, exc)
        return None


def acted_summary(*, name: str, app: Optional[str], status: str) -> str:
    """The line for one action in outside software.

    Reads as what happened rather than as what was called: "Booked the
    appointment in Google Calendar" beats
    "GOOGLECALENDAR_CREATE_EVENT: success". The screen this feeds is read by
    somebody who has never seen an API.
    """
    from api.services.workflow.status_lines import humanise_tool_name

    did = humanise_tool_name(name) or "did something"
    where = f" in {app}" if app else ""
    if status == "error":
        return f"Tried to {did.lower()}{where} and could not"
    return f"{did}{where}"


async def record_action(
    *,
    organization_id: Optional[int],
    workflow_id: Optional[int],
    definition_id: Optional[int],
    workflow_run_id: Optional[int],
    name: str,
    app: Optional[str],
    status: str,
    error: Optional[str] = None,
) -> None:
    """Record a tool action, from the same facts ``app_interactions`` stores.

    Deliberately a second write rather than a replacement. ``app_interactions``
    is the support record -- what a tool returned, how long it took, the error
    verbatim -- and answers "did it try and what came back". This answers "what
    happened on this call", which is a different question asked by a different
    person. Collapsing them would make one of the two answers worse.

    A failed action becomes ``COULD_NOT``, not ``AGENT_ACTED``. That is the
    whole point: a failure that renders as an ordinary line is a failure
    nobody reads.
    """
    failed = status == "error"
    await record(
        organization_id=organization_id,
        kind=(
            AgentEventKind.COULD_NOT.value
            if failed
            else AgentEventKind.AGENT_ACTED.value
        ),
        summary=acted_summary(name=name, app=app, status=status),
        workflow_id=workflow_id,
        definition_id=definition_id,
        workflow_run_id=workflow_run_id,
        payload={
            "tool": name,
            "app": app,
            "status": status,
            **({"error": error[:500]} if failed and error else {}),
        },
    )
