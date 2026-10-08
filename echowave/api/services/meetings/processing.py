"""What happens after Stop (or after an upload, or pasted notes).

Run by the ARQ worker (``tasks/meetings.py``), never in a request:

1. An upload is checked (length, readable) and cut into short pieces; the
   original file is dropped.
2. Every segment still waiting is transcribed. Segments another job is
   already transcribing (live capture sends them while the meeting runs) are
   waited for, up to ``WAIT_SECONDS``; one still stuck after that is marked
   failed and keeps its audio for "Try again".
3. The meeting's state is decided honestly: ``ready`` when every part was
   heard and nothing is missing, ``partial`` when a gap or a failed part
   leaves something out (the record says which), ``failed`` when there are
   no words at all.
4. The words are read for a summary, decisions and suggested actions.

Running it twice is safe: a segment is transcribed once (claimed), and the
reading replaces only suggestions nobody has touched.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from api import constants
from api.db import db_client
from api.services.meetings import reading, transcription

#: How long to wait for segments a live-capture job is still transcribing.
WAIT_SECONDS = 120


async def _split_source(meeting: Any) -> str | None:
    """Turn an upload's one stored file into pieces. Returns a refusal line,
    or None when it is split (or there was nothing to split)."""
    segments = await db_client.meeting_segments(
        meeting.id, organization_id=meeting.organization_id, with_audio=True
    )
    source = next((s for s in segments if s.status == "source"), None)
    if source is None:
        return None
    try:
        pieces = await transcription.split_upload(
            bytes(source.audio or b""), max_seconds=constants.MEETINGS_MAX_MINUTES * 60
        )
    except transcription.SplitRefused as exc:
        await db_client.update_meeting_segment(
            source.id,
            organization_id=meeting.organization_id,
            status="failed",
            error=str(exc),
            audio=None,
        )
        return str(exc)
    for index, (start, end, wav) in enumerate(pieces, start=1):
        await db_client.add_meeting_segment(
            meeting_id=meeting.id,
            organization_id=meeting.organization_id,
            seq=source.seq + index,
            start_ms=start,
            end_ms=end,
            status="pending",
            audio=wav,
            content_type="audio/wav",
        )
    # The original is not kept once it is in pieces.
    await db_client.delete_meeting_segment(
        source.id, organization_id=meeting.organization_id
    )
    if pieces:
        await db_client.update_meeting(
            meeting.id,
            organization_id=meeting.organization_id,
            captured_ms=pieces[-1][1],
        )
    return None


async def _transcribe_all(meeting: Any) -> None:
    org = meeting.organization_id
    for segment in await db_client.meeting_segments(meeting.id, organization_id=org):
        if segment.status == "pending":
            await transcription.transcribe_segment(segment.id, org, meeting.language)
    waited = 0.0
    while waited < WAIT_SECONDS:
        busy = [
            s
            for s in await db_client.meeting_segments(meeting.id, organization_id=org)
            if s.status in ("transcribing", "pending")
        ]
        if not busy:
            return
        for segment in busy:
            if segment.status == "pending":
                await transcription.transcribe_segment(
                    segment.id, org, meeting.language
                )
        await asyncio.sleep(1.0)
        waited += 1.0
    for segment in await db_client.meeting_segments(meeting.id, organization_id=org):
        if segment.status == "transcribing":
            await db_client.update_meeting_segment(
                segment.id,
                organization_id=org,
                status="failed",
                error="This part was not transcribed in time. Try again.",
            )


def words_by_part(segments: list[Any]) -> dict[int, str]:
    return {
        s.seq: (s.corrected_text if s.corrected_text is not None else s.text) or ""
        for s in segments
        if s.status == "done"
    }


def outcome(segments: list[Any], gaps: list[Any]) -> tuple[str, str | None]:
    """The meeting's state from its parts, and the line saying why."""
    done = [s for s in segments if s.status == "done"]
    failed = [s for s in segments if s.status == "failed"]
    heard = [s for s in done if (s.corrected_text or s.text or "").strip()]
    if not heard:
        if failed and failed[0].error:
            return "failed", failed[0].error
        return "failed", "No words were heard in this meeting."
    reasons = []
    if failed:
        reasons.append(
            f"{len(failed)} part{'s' if len(failed) != 1 else ''} could not be "
            "transcribed"
        )
    if gaps:
        reasons.append(f"{len(gaps)} gap{'s' if len(gaps) != 1 else ''} in the capture")
    if reasons:
        return "partial", "; ".join(reasons).capitalize() + "."
    return "ready", None


async def finish(meeting_id: int, organization_id: int) -> str | None:
    """Process a stopped meeting. Returns its final status. Never raises."""
    from api.services import events

    meeting = await db_client.get_meeting_by_id(
        meeting_id, organization_id=organization_id
    )
    if meeting is None or meeting.status not in ("processing",):
        return getattr(meeting, "status", None)
    try:
        refused = await _split_source(meeting)
        if refused is None:
            await _transcribe_all(meeting)
        segments = await db_client.meeting_segments(
            meeting.id, organization_id=organization_id
        )
        gaps = [
            b
            for b in await db_client.meeting_breaks(
                meeting.id, organization_id=organization_id
            )
            if b.kind == "gap"
        ]
        status, why = outcome(segments, gaps)
        if refused is not None:
            status, why = "failed", refused
        await db_client.update_meeting(
            meeting.id,
            organization_id=organization_id,
            status=status,
            status_reason=why,
            reading_status="reading" if status != "failed" else "pending",
        )
        if status == "failed":
            await events.emit(
                "capture_failed",
                user_id=meeting.owner_user_id,
                organization_id=organization_id,
                properties={"audio_source": meeting.source, "reason_code": "no_words"},
            )
            return status
        reading_status, note = await reading.read(meeting, words_by_part(segments))
        await db_client.update_meeting(
            meeting.id,
            organization_id=organization_id,
            reading_status=reading_status,
            reading_note=note,
        )
        await events.emit(
            "meeting_processed",
            user_id=meeting.owner_user_id,
            organization_id=organization_id,
            properties={"status": status, "reason_code": reading_status},
        )
        await _people_note(meeting, organization_id)
        return status
    except Exception as exc:  # noqa: BLE001 - the record must say something
        logger.exception("Processing meeting {} broke: {}", meeting_id, exc)
        await db_client.update_meeting(
            meeting_id,
            organization_id=organization_id,
            status="failed",
            status_reason="Something went wrong on our side while processing. Try again.",
        )
        return "failed"


async def _people_note(meeting, organization_id: int) -> None:
    """Each named participant's page in the owner's People gets the meeting.
    Names only -- a meeting has no numbers -- so a name matches one contact
    exactly or a new contact is added. Nothing while People is off."""
    from api.services.people import enabled as people_enabled
    from api.services.people import interactions as people_interactions
    from api.services.people.normalise import name_key

    if not people_enabled(organization_id) or not meeting.participants:
        return
    # Read again: the summary was written after this row was loaded.
    meeting = (
        await db_client.get_meeting_by_id(meeting.id, organization_id=organization_id)
        or meeting
    )
    summary = meeting.summary if isinstance(meeting.summary, dict) else {}
    first = (summary.get("summary") or [""])[0] if summary.get("summary") else ""
    line = f"Meeting: {meeting.title}" + (f" -- {first}" if first else "")
    for name in (meeting.participants or [])[:30]:
        if not isinstance(name, str) or not name.strip():
            continue
        await people_interactions.record(
            organization_id,
            meeting.owner_user_id,
            channel="meeting",
            direction="both",
            name=name,
            line=line,
            ref=f"meeting:{meeting.id}:{name_key(name)}"[:200],
            at=meeting.created_at,
        )


async def reread(meeting_id: int, organization_id: int) -> str:
    """Read again after the person corrected the transcript."""
    meeting = await db_client.get_meeting_by_id(
        meeting_id, organization_id=organization_id
    )
    if meeting is None:
        return "failed"
    segments = await db_client.meeting_segments(
        meeting.id, organization_id=organization_id
    )
    reading_status, note = await reading.read(meeting, words_by_part(segments))
    await db_client.update_meeting(
        meeting.id,
        organization_id=organization_id,
        reading_status=reading_status,
        reading_note=note,
    )
    return reading_status
