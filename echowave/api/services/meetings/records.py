"""Meeting records: capture moves, the record as the screens read it, and
what a person can do with a saved record.

Every function takes the meeting already read with the person's own scope
(``db_client.get_meeting`` filters on workspace and owner); the routes do
that read and answer 404 for anything else, a colleague's meeting included.

States (screen 11): ``recording``, ``paused``, ``uploading``, ``processing``,
then ``ready``, ``partial`` or ``failed``. Gaps and pauses are rows of their
own (``meeting_breaks``), so the record can show where capture stopped and
why rather than a transcript that silently skips.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from api import constants
from api.db import db_client
from api.services.meetings import follow_ups, reading, transcription

SOURCES = ("microphone", "upload", "notes")
#: How each source is named on screen. Exactly what it is, never more:
#: there is no source for another app's audio or a phone call.
SOURCE_LABELS = {
    "microphone": "This device's microphone",
    "upload": "An uploaded recording",
    "notes": "Pasted notes",
}
GAP_REASONS = {
    "microphone_lost": "The microphone stopped",
    "backgrounded": "The page was in the background",
    "offline": "The connection dropped",
    "recorder_error": "The recorder failed",
    "missing_segment": "This part never arrived",
}
CAPTURING = ("recording", "paused")
FINISHED = ("ready", "partial", "failed")
MAX_NOTES_CHARS = 100_000
MAX_TITLE = 200


class MeetingRefused(ValueError):
    """A request that cannot be done, with the line a person reads, and the
    HTTP status that fits it."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# --- what is set up -----------------------------------------------------------


async def capabilities(organization_id: int) -> dict[str, Any]:
    """Each capture source as available or needs setup, with the reason
    (design, "Capability state"). Read before anything is recorded."""
    sarvam = await transcription.sarvam_setup(organization_id)
    reader = await reading.reader_available(organization_id)
    tools = transcription.ffmpeg_available()
    words = None if sarvam.available else transcription.NEEDS_SETUP

    def state(ok: bool, reason: str | None) -> dict[str, Any]:
        return {
            "state": "available" if ok else "needs_setup",
            "reason": None if ok else reason,
        }

    return {
        "sources": {
            "microphone": state(sarvam.available, words),
            "upload": state(
                sarvam.available and tools,
                words
                or "Uploaded recordings need setup on this server (audio tools are missing).",
            ),
            "notes": state(True, None),
        },
        "transcription": {
            **state(sarvam.available, words),
            "provider": "sarvam",
            "key_source": sarvam.key_source,
        },
        "summary": state(
            reader,
            "The summary needs setup: no text model is available to this "
            "workspace. Transcripts still work.",
        ),
        "languages": transcription.meeting_languages(),
        "max_upload_mb": constants.MEETINGS_MAX_UPLOAD_MB,
        "max_minutes": constants.MEETINGS_MAX_MINUTES,
        "segment_seconds": transcription.SEGMENT_SECONDS,
        # Said plainly so no screen implies more (handoff 23).
        "limits_note": (
            "Records what this device's microphone hears. It cannot record "
            "another app's audio or a phone call."
        ),
        "retention_note": (
            "Audio is not kept: each part is deleted once its words are "
            "transcribed. The words stay until you delete the meeting."
        ),
    }


# --- capture ----------------------------------------------------------------


async def _enqueue(name: str, *args: Any) -> bool:
    from api.tasks.arq import enqueue_job

    try:
        await enqueue_job(name, *args)
        return True
    except Exception as exc:  # noqa: BLE001 - said on the record
        logger.error("Could not queue {} for a meeting: {}", name, exc)
        return False


async def _default_title(user_id: int) -> str:
    timezone = await reading.person_timezone(user_id)
    now = datetime.now(ZoneInfo(timezone))
    return f"Meeting on {now.strftime('%d %b, %H:%M')}"


async def create(
    *,
    organization_id: int,
    user_id: int,
    source: str,
    title: str | None,
    language: str,
    participants: list[str] | None,
    consent_confirmed: bool,
    origin_thread_id: str | None,
    notes: str | None = None,
) -> Any:
    from api.services import events

    if source not in SOURCES:
        raise MeetingRefused("Choose to record, upload or paste notes.", 422)
    if not transcription.is_meeting_language(language):
        raise MeetingRefused("That language cannot be transcribed yet.", 422)
    if source != "notes" and not consent_confirmed:
        # Screen 11: capture starts only after the person confirms.
        raise MeetingRefused(
            "Confirm you have everyone's permission to record before capture starts.",
            422,
        )
    if source == "notes" and not (notes or "").strip():
        raise MeetingRefused("Paste the notes first.", 422)
    if source != "notes":
        caps = await capabilities(organization_id)
        offer = caps["sources"][source]
        if offer["state"] != "available":
            raise MeetingRefused(offer["reason"], 409)
    now = datetime.now(UTC)
    clean_people = [
        str(p).strip()[:80] for p in (participants or []) if str(p).strip()
    ][:30]
    meeting = await db_client.create_meeting(
        public_id=uuid.uuid4().hex,
        organization_id=organization_id,
        owner_user_id=user_id,
        title=(title or "").strip()[:MAX_TITLE] or await _default_title(user_id),
        source=source,
        language=language,
        participants=clean_people or None,
        origin_thread_id=(origin_thread_id or None) and origin_thread_id[:36],
        consent_confirmed_at=now if source != "notes" else None,
        status={
            "microphone": "recording",
            "upload": "uploading",
            "notes": "processing",
        }[source],
        capture_started_at=now if source == "microphone" else None,
        reading_status="pending",
        created_at=now,
        updated_at=now,
    )
    if source == "notes":
        words = notes.strip()[:MAX_NOTES_CHARS]
        await db_client.add_meeting_segment(
            meeting_id=meeting.id,
            organization_id=organization_id,
            seq=0,
            start_ms=0,
            end_ms=0,
            status="done",
            text=words,
            has_action_cue=transcription.has_action_cue(words),
            transcribed_at=now,
        )
        await _start_processing(meeting)
    else:
        await events.emit(
            "capture_started",
            user_id=user_id,
            organization_id=organization_id,
            properties={"audio_source": source, "language": language},
        )
    return await db_client.get_meeting_by_id(
        meeting.id, organization_id=organization_id
    )


async def _start_processing(meeting: Any) -> None:
    from api.tasks.function_names import FunctionNames

    if not await _enqueue(
        FunctionNames.FINISH_MEETING, meeting.id, meeting.organization_id
    ):
        await db_client.update_meeting(
            meeting.id,
            organization_id=meeting.organization_id,
            status_reason="Processing could not start. Try again.",
        )


async def add_segment(
    meeting: Any,
    *,
    seq: int,
    start_ms: int,
    end_ms: int,
    audio: bytes,
    content_type: str | None,
) -> tuple[Any, bool]:
    from api.tasks.function_names import FunctionNames

    if meeting.source != "microphone":
        raise MeetingRefused("This meeting is not being recorded here.")
    if meeting.status not in CAPTURING:
        raise MeetingRefused("This meeting has stopped recording.")
    if seq < 0 or seq > 100_000 or start_ms < 0 or end_ms < start_ms:
        raise MeetingRefused("That part is out of order.", 422)
    if not audio:
        raise MeetingRefused("That part had no audio.", 422)
    if len(audio) > transcription.MAX_SEGMENT_BYTES:
        raise MeetingRefused("That part is too large.", 413)
    if end_ms > constants.MEETINGS_MAX_MINUTES * 60_000:
        raise MeetingRefused(
            f"A meeting can be at most {constants.MEETINGS_MAX_MINUTES} minutes. Stop to save it.",
            409,
        )
    segment, created = await db_client.add_meeting_segment(
        meeting_id=meeting.id,
        organization_id=meeting.organization_id,
        seq=seq,
        start_ms=start_ms,
        end_ms=end_ms,
        status="pending",
        audio=audio,
        content_type=(content_type or "audio/webm")[:64],
    )
    if created:
        await db_client.update_meeting(
            meeting.id,
            organization_id=meeting.organization_id,
            captured_ms=max(int(meeting.captured_ms or 0), end_ms),
        )
        # Transcribed while the meeting goes on, for the live transcript.
        await _enqueue(
            FunctionNames.TRANSCRIBE_MEETING_SEGMENT,
            segment.id,
            meeting.organization_id,
        )
    return segment, created


async def add_upload(
    meeting: Any, *, filename: str | None, content_type: str | None, audio: bytes
) -> Any:
    if meeting.source != "upload":
        raise MeetingRefused("This meeting is not an upload.")
    if meeting.status != "uploading":
        raise MeetingRefused("A recording was already uploaded for this meeting.")
    if not audio:
        raise MeetingRefused("That file is empty.", 422)
    if len(audio) > constants.MEETINGS_MAX_UPLOAD_MB * 1024 * 1024:
        raise MeetingRefused(
            f"That file is larger than {constants.MEETINGS_MAX_UPLOAD_MB} MB.", 413
        )
    kind = (content_type or "").lower()
    if kind and not (kind.startswith("audio/") or kind.startswith("video/")):
        raise MeetingRefused("Upload an audio recording.", 415)
    await db_client.add_meeting_segment(
        meeting_id=meeting.id,
        organization_id=meeting.organization_id,
        seq=0,
        start_ms=0,
        end_ms=0,
        status="source",
        audio=audio,
        content_type=kind[:64] or None,
    )
    updated = await db_client.update_meeting(
        meeting.id,
        organization_id=meeting.organization_id,
        only_if_status=("uploading",),
        status="processing",
        upload_name=(filename or "recording")[:200],
        capture_ended_at=datetime.now(UTC),
    )
    if updated is None:
        raise MeetingRefused("A recording was already uploaded for this meeting.")
    await _start_processing(updated)
    return updated


async def pause(meeting: Any) -> Any:
    if meeting.status != "recording":
        raise MeetingRefused("Only a recording meeting can be paused.")
    return (
        await db_client.update_meeting(
            meeting.id,
            organization_id=meeting.organization_id,
            only_if_status=("recording",),
            status="paused",
        )
        or meeting
    )


async def resume(meeting: Any, *, at_ms: int, paused_ms: int | None) -> Any:
    if meeting.status != "paused":
        raise MeetingRefused("Only a paused meeting can be resumed.")
    await db_client.add_meeting_break(
        meeting_id=meeting.id,
        organization_id=meeting.organization_id,
        kind="pause",
        at_ms=max(0, at_ms),
        duration_ms=max(0, paused_ms) if paused_ms is not None else None,
    )
    return (
        await db_client.update_meeting(
            meeting.id,
            organization_id=meeting.organization_id,
            only_if_status=("paused",),
            status="recording",
        )
        or meeting
    )


async def add_gap(
    meeting: Any, *, at_ms: int, duration_ms: int | None, reason: str
) -> Any:
    if meeting.status not in CAPTURING:
        raise MeetingRefused("This meeting has stopped recording.")
    if reason not in GAP_REASONS or reason == "missing_segment":
        raise MeetingRefused("Say why capture stopped.", 422)
    return await db_client.add_meeting_break(
        meeting_id=meeting.id,
        organization_id=meeting.organization_id,
        kind="gap",
        reason=reason,
        at_ms=max(0, at_ms),
        duration_ms=max(0, duration_ms) if duration_ms is not None else None,
    )


async def stop(meeting: Any, *, last_seq: int | None, captured_ms: int | None) -> Any:
    """Stop capture and start processing. Any segment the client says it
    made and the server never received is a gap on the record."""
    if meeting.status not in CAPTURING:
        raise MeetingRefused("This meeting has already stopped.")
    updated = await db_client.update_meeting(
        meeting.id,
        organization_id=meeting.organization_id,
        only_if_status=CAPTURING,
        status="processing",
        last_seq=last_seq,
        captured_ms=max(int(meeting.captured_ms or 0), int(captured_ms or 0)),
        capture_ended_at=datetime.now(UTC),
    )
    if updated is None:
        raise MeetingRefused("This meeting has already stopped.")
    segments = await db_client.meeting_segments(
        meeting.id, organization_id=meeting.organization_id
    )
    have = {s.seq: s for s in segments}
    if last_seq is not None:
        previous_end = 0
        for seq in range(0, max(-1, min(last_seq, 100_000)) + 1):
            if seq in have:
                previous_end = have[seq].end_ms
                continue
            await db_client.add_meeting_break(
                meeting_id=meeting.id,
                organization_id=meeting.organization_id,
                kind="gap",
                reason="missing_segment",
                at_ms=previous_end,
                duration_ms=None,
            )
    await _start_processing(updated)
    return updated


async def retry(meeting: Any) -> Any:
    """Try the parts that failed again, and read again."""
    if meeting.status not in FINISHED and meeting.status != "processing":
        raise MeetingRefused("Stop the meeting first.")
    org = meeting.organization_id
    segments = await db_client.meeting_segments(
        meeting.id, organization_id=org, with_audio=True
    )
    retried = 0
    for segment in segments:
        if segment.status == "failed" and segment.audio:
            await db_client.update_meeting_segment(
                segment.id, organization_id=org, status="pending", error=None
            )
            retried += 1
    updated = await db_client.update_meeting(
        meeting.id, organization_id=org, status="processing", status_reason=None
    )
    await _start_processing(updated)
    return updated


# --- the saved record ---------------------------------------------------------


async def rename(meeting: Any, title: str) -> Any:
    words = (title or "").strip()[:MAX_TITLE]
    if not words:
        raise MeetingRefused("Give the meeting a name.", 422)
    return await db_client.update_meeting(
        meeting.id, organization_id=meeting.organization_id, title=words
    )


async def correct(meeting: Any, *, seq: int, text: str) -> Any:
    """A person's correction of one part (screen 12). The original words are
    kept beside it; the summary is not rewritten until they ask."""
    if meeting.status in CAPTURING:
        raise MeetingRefused("Correct the transcript after capture.")
    segments = await db_client.meeting_segments(
        meeting.id, organization_id=meeting.organization_id
    )
    segment = next((s for s in segments if s.seq == seq and s.status == "done"), None)
    if segment is None:
        raise MeetingRefused("That part of the transcript is not here.", 404)
    words = (text or "").strip()
    await db_client.update_meeting_segment(
        segment.id,
        organization_id=meeting.organization_id,
        corrected_text=None
        if words == (segment.text or "").strip()
        else words[:20_000],
        has_action_cue=transcription.has_action_cue(words),
    )
    return await db_client.update_meeting(
        meeting.id, organization_id=meeting.organization_id
    )


def _when(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


async def as_record(meeting: Any) -> dict[str, Any]:
    """The record as screens 11 and 12 read it."""
    org = meeting.organization_id
    segments = await db_client.meeting_segments(meeting.id, organization_id=org)
    breaks = await db_client.meeting_breaks(meeting.id, organization_id=org)
    items = await db_client.meeting_items(meeting.id, organization_id=org)
    actions = []
    for item in items:
        if item.kind != "action":
            continue
        card = follow_ups.card_view(await follow_ups.card_of(org, item))
        actions.append({**_item(item), "card": card})
    return {
        "id": meeting.public_id,
        "title": meeting.title,
        "source": meeting.source,
        "source_label": SOURCE_LABELS.get(meeting.source, meeting.source),
        "language": meeting.language,
        "participants": list(meeting.participants or []),
        "status": meeting.status,
        "status_reason": meeting.status_reason,
        "captured_ms": int(meeting.captured_ms or 0),
        "created_at": _when(meeting.created_at),
        "capture_started_at": _when(meeting.capture_started_at),
        "capture_ended_at": _when(meeting.capture_ended_at),
        "consent_confirmed_at": _when(meeting.consent_confirmed_at),
        "origin_thread_id": meeting.origin_thread_id,
        "upload_name": meeting.upload_name,
        "revision": int(meeting.revision or 1),
        "reading_status": meeting.reading_status,
        "reading_note": meeting.reading_note,
        "summary": list(meeting.summary or []),
        "transcript": [
            {
                "seq": s.seq,
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "status": "waiting" if s.status == "source" else s.status,
                "text": (s.corrected_text if s.corrected_text is not None else s.text)
                or "",
                "original_text": s.text if s.corrected_text is not None else None,
                "corrected": s.corrected_text is not None,
                "error": s.error,
                "has_action_cue": bool(s.has_action_cue),
            }
            for s in segments
        ],
        "breaks": [
            {
                "kind": b.kind,
                "reason": b.reason,
                "reason_label": GAP_REASONS.get(b.reason or "", "Paused")
                if b.kind == "gap"
                else "Paused",
                "at_ms": b.at_ms,
                "duration_ms": b.duration_ms,
            }
            for b in breaks
        ],
        "decisions": [_item(item) for item in items if item.kind == "decision"],
        "actions": actions,
        "possible_actions": [
            {"seq": s.seq, "text": ((s.corrected_text or s.text) or "")[:160]}
            for s in segments
            if s.has_action_cue
        ][-5:],
    }


def _item(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind,
        "text": item.text,
        "owner_name": item.owner_name,
        "due_text": item.due_text,
        "due_at": _when(item.due_at),
        "confidence": item.confidence,
        "missing": list(item.missing or []),
        "segment_seq": item.segment_seq,
        "excerpt": item.excerpt,
        "source_found": bool(item.source_found),
        "edited": bool(item.edited),
        "task_id": item.task_id,
    }


def summary_row(meeting: Any) -> dict[str, Any]:
    return {
        "id": meeting.public_id,
        "title": meeting.title,
        "source": meeting.source,
        "status": meeting.status,
        "captured_ms": int(meeting.captured_ms or 0),
        "created_at": _when(meeting.created_at),
        "reading_status": meeting.reading_status,
    }


def _clock(ms: int | None) -> str:
    seconds = max(0, int(ms or 0) // 1000)
    return f"{seconds // 3600:d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def _break_line(item: dict[str, Any]) -> str:
    what = f"Gap: {item['reason_label']}" if item["kind"] == "gap" else "Paused"
    return f"> [{_clock(item['at_ms'])}] {what}"


async def export_markdown(meeting: Any) -> str:
    record = await as_record(meeting)
    lines = [
        f"# {record['title']}",
        "",
        f"- Recorded from: {record['source_label']}",
        f"- Language: {record['language']}",
        f"- Captured: {_clock(record['captured_ms'])}",
        f"- Status: {record['status']}"
        + (f" ({record['status_reason']})" if record["status_reason"] else ""),
        "",
        "## Summary",
        "",
        *([f"- {line}" for line in record["summary"]] or ["(No summary.)"]),
        "",
        "## Decisions",
        "",
        *(
            [f"- {d['text']} (part {d['segment_seq']})" for d in record["decisions"]]
            or ["(None found.)"]
        ),
        "",
        "## Actions",
        "",
    ]
    for action in record["actions"]:
        state = (action.get("card") or {}).get("state") or "suggested"
        lines.append(
            f"- {action['text']} -- owner: {action['owner_name'] or 'not named'}; "
            f"when: {action['due_text'] or 'not said'}; {state}"
        )
    if not record["actions"]:
        lines.append("(None found.)")
    lines += ["", "## Transcript", ""]
    gaps = sorted(record["breaks"], key=lambda b: b["at_ms"])
    for part in record["transcript"]:
        for gap in [g for g in gaps if g["at_ms"] <= part["start_ms"]]:
            lines.append(_break_line(gap))
            gaps.remove(gap)
        if part["status"] == "done":
            lines.append(f"[{_clock(part['start_ms'])}] {part['text']}")
        else:
            lines.append(
                f"[{_clock(part['start_ms'])}] (not transcribed: {part['error'] or part['status']})"
            )
    for gap in gaps:
        lines.append(_break_line(gap))
    return "\n".join(lines) + "\n"


async def deletion_preview(meeting: Any) -> dict[str, Any]:
    """What deleting it does (screen 12, handoff 23 "Saved records"): which
    tasks it made and whether they stay, and what happens to memory."""
    org = meeting.organization_id
    tasks = []
    waiting = 0
    for item in await db_client.meeting_items(
        meeting.id, organization_id=org, kind="action"
    ):
        card = await follow_ups.card_of(org, item)
        state = (card.payload or {}).get("state") if card is not None else None
        if state in ("proposed", "armed"):
            waiting += 1
        if item.task_id:
            task = await db_client.get_task(item.task_id, organization_id=org)
            if task is not None:
                tasks.append(
                    {
                        "task_id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "can_cancel": task.status in ("todo", "backlog"),
                    }
                )
    return {
        "linked_tasks": tasks,
        "waiting_cards": waiting,
        "memory": "Nothing from this meeting was saved to memory, so there is nothing to forget.",
        "cards_note": (
            "Waiting approvals are withdrawn. The approval history keeps each "
            "task's wording; the meeting's words are removed."
        ),
    }


async def delete(meeting: Any, *, user_id: int, cancel_tasks: bool) -> dict[str, Any]:
    from api.enums import AgentEventKind
    from api.services.meetings import thread_for
    from api.services.workflow import actions

    org = meeting.organization_id
    # Every card the meeting ever had -- the live ones and those an edit
    # withdrew -- is on its own thread: withdraw what still waits, and take
    # the meeting's words out of each.
    cards = await db_client.agent_events(
        organization_id=org,
        kinds=[AgentEventKind.ACTION_PROPOSED.value],
        assistant_thread=True,
        thread_id=thread_for(meeting.public_id),
        limit=500,
    )
    for card in cards or []:
        verb = {"proposed": "decline", "armed": "undo"}.get(
            (card.payload or {}).get("state")
        )
        if verb:
            try:
                await actions.settle(
                    organization_id=org, event_id=card.id, verb=verb, user_id=user_id
                )
            except actions.ActionError as exc:
                logger.info("Card {} moved on during delete: {}", card.id, exc)
        fresh = await db_client.get_agent_event(card.id, organization_id=org)
        payload = dict((fresh or card).payload or {})
        args = dict(payload.get("args") or {})
        if args.get("excerpt"):
            args["excerpt"] = ""
            payload["args"] = args
            await db_client.set_agent_event_payload(
                card.id, organization_id=org, payload=payload
            )
    cancelled = kept = 0
    for item in await db_client.meeting_items(
        meeting.id, organization_id=org, kind="action"
    ):
        if not item.task_id:
            continue
        task = await db_client.get_task(item.task_id, organization_id=org)
        if task is None:
            continue
        if cancel_tasks and task.status in ("todo", "backlog"):
            await follow_ups.cancel_task(org, task, reason_code="meeting_deleted")
            cancelled += 1
        else:
            kept += 1
    await db_client.delete_meeting(meeting.id, organization_id=org)
    return {"deleted": True, "tasks_cancelled": cancelled, "tasks_kept": kept}
