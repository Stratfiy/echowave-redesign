"""What a huddle leaves behind: its transcript, and the agent's notes.

**One event per huddle.** A ``huddle`` row on the agent's thread, created at
the first line and rewritten as the conversation goes, so the thread holds a
compact record of who said what -- and the edit cards the huddle produced
sit beside it, as cards always do. Rewritten rather than appended because
twenty voice turns as twenty rows would bury the thread they were meant to
annotate. A reconnect finds the same row by the session's id and carries on.

**Teammate memory.** Short notes the agent keeps about how this person wants
it to work ("Priya wants the escalation numbers weekly, not daily"), written
by the agent's ``remember`` tool into that person's huddle row and read back
at the next huddle. They live here, not in ``organisation_facts``, because
anything there can reach a prompt a customer hears; huddle notes reach only
the next huddle. Bounded twice: per note (``MAX_NOTE_CHARS``) and per load
(``MAX_NOTES``, newest first, from the last ``NOTE_EVENTS`` huddles).

Every read and write is by organisation, and notes by person too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind

KIND = AgentEventKind.HUDDLE.value

#: A huddle keeps this many lines on its row; older ones are counted.
MAX_TURNS = 60
#: Each line is a spoken sentence or three, not a document.
MAX_LINE_CHARS = 600
MAX_NOTE_CHARS = 200
#: Notes read into the next huddle.
MAX_NOTES = 12
#: Notes one huddle may write.
MAX_NOTES_PER_HUDDLE = 6
#: How many of a person's recent huddles with an agent notes are read from.
NOTE_EVENTS = 20


def _now() -> str:
    return datetime.now(UTC).isoformat()


def clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def _huddle_rows(organization_id: int, workflow_id: int, limit: int) -> list:
    return list(
        await db_client.agent_events(
            organization_id=organization_id,
            workflow_id=workflow_id,
            kinds=[KIND],
            limit=limit,
        )
    )


async def find(
    *, organization_id: int, workflow_id: int, session_id: int
) -> tuple[int, dict[str, Any]] | None:
    """The row of this huddle, when an earlier connection wrote one."""
    for row in await _huddle_rows(organization_id, workflow_id, NOTE_EVENTS):
        payload = dict(row.payload or {})
        if payload.get("voice_session_id") == session_id:
            return row.id, payload
    return None


def new_payload(*, session_id: int, user_id: int) -> dict[str, Any]:
    return {
        "voice_session_id": session_id,
        "user_id": user_id,
        "turns": [],
        "dropped": 0,
        "notes": [],
        "cards": [],
        "started_at": _now(),
    }


def add_turn(
    payload: dict[str, Any],
    who: str,
    text: str,
    *,
    interrupted: bool = False,
    whisper_run_id: int | None = None,
) -> None:
    """Append a line, keeping the row bounded. ``whisper_run_id``: the line
    went to that live call as a whisper ("This call only")."""
    line = clip(text, MAX_LINE_CHARS)
    if not line:
        return
    turns = list(payload.get("turns") or [])
    entry: dict[str, Any] = {"who": who, "text": line, "at": _now()}
    if interrupted:
        entry["interrupted"] = True
    if whisper_run_id is not None:
        entry["whisper_run_id"] = int(whisper_run_id)
    turns.append(entry)
    if len(turns) > MAX_TURNS:
        payload["dropped"] = int(payload.get("dropped") or 0) + len(turns) - MAX_TURNS
        turns = turns[-MAX_TURNS:]
    payload["turns"] = turns


def add_note(payload: dict[str, Any], note: str) -> bool:
    """Keep a note on this huddle; False when it is empty, a repeat or one
    too many."""
    note = clip(note, MAX_NOTE_CHARS)
    notes = list(payload.get("notes") or [])
    if not note or note.casefold() in {n.casefold() for n in notes}:
        return False
    if len(notes) >= MAX_NOTES_PER_HUDDLE:
        return False
    notes.append(note)
    payload["notes"] = notes
    return True


async def save(
    *,
    organization_id: int,
    workflow_id: int,
    agent_name: str,
    event_id: int | None,
    payload: dict[str, Any],
) -> int | None:
    """Write the row: create it the first time, rewrite it after. Returns
    its id. Never raises -- a transcript must not end the conversation."""
    try:
        if event_id is None:
            return await _create(
                organization_id=organization_id,
                workflow_id=workflow_id,
                agent_name=agent_name,
                payload=payload,
            )
        await db_client.set_agent_event_payload(
            event_id, organization_id=organization_id, payload=payload
        )
        return event_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write the huddle transcript: {}", exc)
        return event_id


async def _create(
    *,
    organization_id: int,
    workflow_id: int,
    agent_name: str,
    payload: dict[str, Any],
) -> int | None:
    from api.services.workflow import agent_timeline

    return await agent_timeline.record(
        organization_id=organization_id,
        kind=KIND,
        actor=AgentEventActor.HUMAN.value,
        summary=f"Huddle with {agent_name}",
        workflow_id=workflow_id,
        payload=payload,
        # The agent's own thread, as a direct message is: a huddle is not
        # said in the channel the agent sits in.
        in_channel=False,
    )


async def notes_for(
    *, organization_id: int, user_id: int, workflow_id: int
) -> list[str]:
    """This person's notes with this agent, newest first, bounded."""
    out: list[str] = []
    seen: set[str] = set()
    try:
        rows = await _huddle_rows(organization_id, workflow_id, NOTE_EVENTS)
    except Exception as exc:  # noqa: BLE001 - a huddle without notes still runs
        logger.warning("Could not read huddle notes: {}", exc)
        return []
    for row in rows:
        payload = row.payload or {}
        if payload.get("user_id") != user_id:
            continue
        for note in reversed(list(payload.get("notes") or [])):
            key = str(note).casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(clip(str(note), MAX_NOTE_CHARS))
            if len(out) >= MAX_NOTES:
                return out
    return out


async def forget_notes(*, organization_id: int, user_id: int, workflow_id: int) -> int:
    """Clear this person's notes with this agent. Returns how many went.

    Only the notes: the transcript stays, because it is the thread's record
    of what was said, not the agent's memory of it."""
    forgotten = 0
    for row in await _huddle_rows(organization_id, workflow_id, NOTE_EVENTS):
        payload = dict(row.payload or {})
        if payload.get("user_id") != user_id or not payload.get("notes"):
            continue
        forgotten += len(payload["notes"])
        payload["notes"] = []
        payload["notes_forgotten_at"] = _now()
        await db_client.set_agent_event_payload(
            row.id, organization_id=organization_id, payload=payload
        )
    return forgotten
