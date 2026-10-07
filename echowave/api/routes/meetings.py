"""Launch stream `meetings`: meeting capture (screen 11) and the meeting
record with its follow-ups (screen 12).

Thin by design (api/AGENTS.md): each handler resolves the person and their
workspace, reads the meeting with both (so a colleague's meeting is a 404,
the way a wrong tenant is), and delegates to ``services/meetings``. Every
route is a 404 while ``meeting_capture`` is off for the workspace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api import constants
from api.db import db_client
from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.meetings import FLAG, follow_ups, processing, records

router = APIRouter(
    prefix="/meetings",
    tags=["meetings"],
    dependencies=[Depends(features.require(FLAG, per_organization=True))],
)


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class CapabilityLine(BaseModel):
    #: ``available`` or ``needs_setup``.
    state: str
    reason: Optional[str] = None


class TranscriptionCapability(CapabilityLine):
    provider: str
    #: ``workspace`` or ``platform``: whose Sarvam key; never the key.
    key_source: Optional[str] = None


class MeetingLanguage(BaseModel):
    code: str
    native: str
    english: str


class MeetingCapabilities(BaseModel):
    sources: dict[str, CapabilityLine]
    transcription: TranscriptionCapability
    summary: CapabilityLine
    languages: list[MeetingLanguage]
    max_upload_mb: int
    max_minutes: int
    segment_seconds: int
    limits_note: str
    retention_note: str


class TranscriptPart(BaseModel):
    seq: int
    start_ms: int
    end_ms: int
    #: ``pending`` | ``transcribing`` | ``done`` | ``failed`` | ``waiting``.
    status: str
    text: str
    original_text: Optional[str] = None
    corrected: bool
    error: Optional[str] = None
    has_action_cue: bool


class MeetingBreak(BaseModel):
    #: ``pause`` or ``gap``.
    kind: str
    reason: Optional[str] = None
    reason_label: str
    at_ms: int
    duration_ms: Optional[int] = None


class CardArgs(BaseModel):
    task: Optional[str] = None
    owner_name: Optional[str] = None
    due_at: Optional[str] = None
    due_text: Optional[str] = None
    excerpt: Optional[str] = None


class FollowUpCard(BaseModel):
    """The action card behind one suggested action (controls)."""

    event_id: int
    #: The card's own states: proposed, armed, running, done, failed,
    #: declined, cancelled, undone, outcome_unknown.
    state: Optional[str] = None
    #: The payload version a Confirm must name (task ledger on), else null.
    version: Optional[str] = None
    revision: int
    label: Optional[str] = None
    effect: Optional[str] = None
    fires_at: Optional[str] = None
    error: Optional[str] = None
    done_note: Optional[str] = None
    task_id: Optional[int] = None
    args: CardArgs


class MeetingItem(BaseModel):
    id: int
    kind: str
    text: str
    owner_name: Optional[str] = None
    due_text: Optional[str] = None
    due_at: Optional[str] = None
    confidence: Optional[str] = None
    missing: list[str]
    segment_seq: Optional[int] = None
    excerpt: Optional[str] = None
    source_found: bool
    edited: bool
    task_id: Optional[int] = None


class MeetingAction(MeetingItem):
    card: Optional[FollowUpCard] = None


class PossibleAction(BaseModel):
    seq: int
    text: str


class MeetingRecord(BaseModel):
    id: str
    title: str
    source: str
    source_label: str
    language: str
    participants: list[str]
    status: str
    status_reason: Optional[str] = None
    captured_ms: int
    created_at: Optional[str] = None
    capture_started_at: Optional[str] = None
    capture_ended_at: Optional[str] = None
    consent_confirmed_at: Optional[str] = None
    origin_thread_id: Optional[str] = None
    upload_name: Optional[str] = None
    revision: int
    reading_status: str
    reading_note: Optional[str] = None
    summary: list[str]
    transcript: list[TranscriptPart]
    breaks: list[MeetingBreak]
    decisions: list[MeetingItem]
    actions: list[MeetingAction]
    possible_actions: list[PossibleAction]


class MeetingSummary(BaseModel):
    id: str
    title: str
    source: str
    status: str
    captured_ms: int
    created_at: Optional[str] = None
    reading_status: str


class MeetingList(BaseModel):
    meetings: list[MeetingSummary]


class CreateMeetingRequest(BaseModel):
    source: Literal["microphone", "upload", "notes"]
    title: Optional[str] = Field(default=None, max_length=200)
    language: str = Field(default="unknown", max_length=16)
    participants: Optional[list[str]] = Field(default=None, max_length=30)
    #: The person confirmed they have the participants' permission. Required
    #: for any audio source.
    consent_confirmed: bool = False
    origin_thread_id: Optional[str] = Field(default=None, max_length=36)
    notes: Optional[str] = Field(default=None, max_length=records.MAX_NOTES_CHARS)


class ResumeRequest(BaseModel):
    at_ms: int = Field(ge=0)
    paused_ms: Optional[int] = Field(default=None, ge=0)


class GapRequest(BaseModel):
    at_ms: int = Field(ge=0)
    duration_ms: Optional[int] = Field(default=None, ge=0)
    reason: Literal["microphone_lost", "backgrounded", "offline", "recorder_error"]


class StopRequest(BaseModel):
    #: The highest segment number the client made (-1 for none).
    last_seq: Optional[int] = Field(default=None, ge=-1)
    captured_ms: Optional[int] = Field(default=None, ge=0)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class CorrectRequest(BaseModel):
    text: str = Field(max_length=20_000)


class EditItemRequest(BaseModel):
    text: Optional[str] = Field(default=None, max_length=300)
    owner_name: Optional[str] = Field(default=None, max_length=120)
    due_at: Optional[datetime] = None
    due_text: Optional[str] = Field(default=None, max_length=120)


class SettleItemRequest(BaseModel):
    verb: Literal["confirm", "decline", "undo"]
    #: The card version the person was shown (task ledger).
    version: Optional[str] = Field(default=None, max_length=32)


class LinkedTask(BaseModel):
    task_id: int
    title: str
    status: str
    can_cancel: bool


class DeletionPreview(BaseModel):
    linked_tasks: list[LinkedTask]
    waiting_cards: int
    memory: str
    cards_note: str


class DeletionResult(BaseModel):
    deleted: bool
    tasks_cancelled: int
    tasks_kept: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _org(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


async def _mine(user: UserModel, meeting_id: str) -> Any:
    meeting = await db_client.get_meeting(
        meeting_id[:32], organization_id=_org(user), owner_user_id=user.id
    )
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


async def _record(meeting: Any) -> MeetingRecord:
    fresh = await db_client.get_meeting_by_id(
        meeting.id, organization_id=meeting.organization_id
    )
    return MeetingRecord(**await records.as_record(fresh or meeting))


def _refused(exc: records.MeetingRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


async def _read_capped(file: UploadFile, limit: int) -> bytes:
    """Read at most ``limit`` bytes; one more means too large, said before
    anything is stored."""
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"That file is larger than {limit // (1024 * 1024)} MB.",
        )
    return data


# ---------------------------------------------------------------------------
# Screen 11: capture
# ---------------------------------------------------------------------------


@router.get("/capabilities", response_model=MeetingCapabilities)
async def meeting_capabilities(
    user: UserModel = Depends(get_user),
) -> MeetingCapabilities:
    """What can be captured here, and what needs setup, before anything is
    recorded."""
    return MeetingCapabilities(**await records.capabilities(_org(user)))


@router.get("", response_model=MeetingList)
async def list_meetings(
    limit: int = Query(default=50, ge=1, le=200),
    user: UserModel = Depends(get_user),
) -> MeetingList:
    """The person's own meetings in this workspace, newest first."""
    rows = await db_client.list_meetings(
        organization_id=_org(user), owner_user_id=user.id, limit=limit
    )
    return MeetingList(
        meetings=[MeetingSummary(**records.summary_row(r)) for r in rows]
    )


@router.post("", response_model=MeetingRecord, status_code=201)
async def create_meeting(
    body: CreateMeetingRequest, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    """Start a meeting: record on this device, upload a recording, or paste
    notes. Audio needs the participants' permission confirmed first."""
    try:
        meeting = await records.create(
            organization_id=_org(user),
            user_id=user.id,
            source=body.source,
            title=body.title,
            language=body.language,
            participants=body.participants,
            consent_confirmed=body.consent_confirmed,
            origin_thread_id=body.origin_thread_id,
            notes=body.notes,
        )
    except records.MeetingRefused as exc:
        raise _refused(exc) from exc
    return await _record(meeting)


@router.get("/{meeting_id}", response_model=MeetingRecord)
async def get_meeting(
    meeting_id: str, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    return await _record(await _mine(user, meeting_id))


@router.post("/{meeting_id}/segments", response_model=MeetingRecord)
async def add_segment(
    meeting_id: str,
    file: UploadFile = File(...),
    seq: int = Form(..., ge=0),
    start_ms: int = Form(..., ge=0),
    end_ms: int = Form(..., ge=0),
    user: UserModel = Depends(get_user),
) -> MeetingRecord:
    """One piece of live capture. Sending the same number again is safe."""
    meeting = await _mine(user, meeting_id)
    from api.services.meetings import transcription

    audio = await _read_capped(file, transcription.MAX_SEGMENT_BYTES)
    try:
        await records.add_segment(
            meeting,
            seq=seq,
            start_ms=start_ms,
            end_ms=end_ms,
            audio=audio,
            content_type=file.content_type,
        )
    except records.MeetingRefused as exc:
        raise _refused(exc) from exc
    return await _record(meeting)


@router.post("/{meeting_id}/upload", response_model=MeetingRecord)
async def upload_recording(
    meeting_id: str,
    file: UploadFile = File(...),
    user: UserModel = Depends(get_user),
) -> MeetingRecord:
    """The recording for an upload meeting. The size is checked before it is
    stored; the length is checked before any of it is transcribed."""
    meeting = await _mine(user, meeting_id)
    audio = await _read_capped(file, constants.MEETINGS_MAX_UPLOAD_MB * 1024 * 1024)
    try:
        await records.add_upload(
            meeting, filename=file.filename, content_type=file.content_type, audio=audio
        )
    except records.MeetingRefused as exc:
        raise _refused(exc) from exc
    return await _record(meeting)


@router.post("/{meeting_id}/pause", response_model=MeetingRecord)
async def pause_meeting(
    meeting_id: str, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    try:
        meeting = await records.pause(await _mine(user, meeting_id))
    except records.MeetingRefused as exc:
        raise _refused(exc) from exc
    return await _record(meeting)


@router.post("/{meeting_id}/resume", response_model=MeetingRecord)
async def resume_meeting(
    meeting_id: str, body: ResumeRequest, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    try:
        meeting = await records.resume(
            await _mine(user, meeting_id), at_ms=body.at_ms, paused_ms=body.paused_ms
        )
    except records.MeetingRefused as exc:
        raise _refused(exc) from exc
    return await _record(meeting)


@router.post("/{meeting_id}/gaps", response_model=MeetingRecord)
async def report_gap(
    meeting_id: str, body: GapRequest, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    """Capture stopped without the person choosing it: the microphone was
    lost, the page went to the background, the connection dropped."""
    meeting = await _mine(user, meeting_id)
    try:
        await records.add_gap(
            meeting, at_ms=body.at_ms, duration_ms=body.duration_ms, reason=body.reason
        )
    except records.MeetingRefused as exc:
        raise _refused(exc) from exc
    return await _record(meeting)


@router.post("/{meeting_id}/stop", response_model=MeetingRecord)
async def stop_meeting(
    meeting_id: str, body: StopRequest, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    try:
        meeting = await records.stop(
            await _mine(user, meeting_id),
            last_seq=body.last_seq,
            captured_ms=body.captured_ms,
        )
    except records.MeetingRefused as exc:
        raise _refused(exc) from exc
    return await _record(meeting)


@router.post("/{meeting_id}/retry", response_model=MeetingRecord)
async def retry_meeting(
    meeting_id: str, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    """Try the parts that could not be transcribed again, and read again."""
    try:
        meeting = await records.retry(await _mine(user, meeting_id))
    except records.MeetingRefused as exc:
        raise _refused(exc) from exc
    return await _record(meeting)


# ---------------------------------------------------------------------------
# Screen 12: the record
# ---------------------------------------------------------------------------


@router.patch("/{meeting_id}", response_model=MeetingRecord)
async def rename_meeting(
    meeting_id: str, body: RenameRequest, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    try:
        meeting = await records.rename(await _mine(user, meeting_id), body.title)
    except records.MeetingRefused as exc:
        raise _refused(exc) from exc
    return await _record(meeting)


@router.put("/{meeting_id}/transcript/{seq}", response_model=MeetingRecord)
async def correct_transcript(
    meeting_id: str, seq: int, body: CorrectRequest, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    """Correct one part of the transcript; the original is kept beside it."""
    try:
        meeting = await records.correct(
            await _mine(user, meeting_id), seq=seq, text=body.text
        )
    except records.MeetingRefused as exc:
        raise _refused(exc) from exc
    return await _record(meeting)


@router.post("/{meeting_id}/read", response_model=MeetingRecord)
async def read_again(
    meeting_id: str, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    """Write the summary again from the (corrected) transcript. Suggestions a
    person already edited or confirmed stay as they are."""
    meeting = await _mine(user, meeting_id)
    if meeting.status not in ("ready", "partial"):
        raise HTTPException(
            status_code=409, detail="There is no transcript to read yet."
        )
    await processing.reread(meeting.id, meeting.organization_id)
    return await _record(meeting)


@router.get("/{meeting_id}/export", response_class=PlainTextResponse)
async def export_meeting(
    meeting_id: str, user: UserModel = Depends(get_user)
) -> PlainTextResponse:
    """The record as Markdown: summary, decisions, actions and transcript,
    with every gap marked."""
    meeting = await _mine(user, meeting_id)
    text = await records.export_markdown(meeting)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in meeting.title)[:60]
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe or "meeting"}.md"'
        },
    )


@router.get("/{meeting_id}/deletion", response_model=DeletionPreview)
async def deletion_preview(
    meeting_id: str, user: UserModel = Depends(get_user)
) -> DeletionPreview:
    """What deleting it would do to the tasks it made, before it is done."""
    return DeletionPreview(
        **await records.deletion_preview(await _mine(user, meeting_id))
    )


@router.delete("/{meeting_id}", response_model=DeletionResult)
async def delete_meeting(
    meeting_id: str,
    cancel_tasks: bool = Query(default=False),
    user: UserModel = Depends(get_user),
) -> DeletionResult:
    meeting = await _mine(user, meeting_id)
    return DeletionResult(
        **await records.delete(meeting, user_id=user.id, cancel_tasks=cancel_tasks)
    )


async def _item(meeting: Any, item_id: int) -> Any:
    item = await db_client.get_meeting_item(
        item_id, meeting_id=meeting.id, organization_id=meeting.organization_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="That action is not here")
    return item


@router.put("/{meeting_id}/actions/{item_id}", response_model=MeetingRecord)
async def edit_action(
    meeting_id: str,
    item_id: int,
    body: EditItemRequest,
    user: UserModel = Depends(get_user),
) -> MeetingRecord:
    """Edit the task, owner or time before confirming. A card already
    waiting is withdrawn: an edit never carries an old approval."""
    meeting = await _mine(user, meeting_id)
    item = await _item(meeting, item_id)
    try:
        await follow_ups.edit(
            user_id=user.id,
            meeting=meeting,
            item=item,
            changes=body.model_dump(exclude_unset=True),
        )
    except follow_ups.FollowUpError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _record(meeting)


@router.post("/{meeting_id}/actions/{item_id}/review", response_model=MeetingRecord)
async def review_action(
    meeting_id: str, item_id: int, user: UserModel = Depends(get_user)
) -> MeetingRecord:
    """Put one suggested action on its own action card: the exact preview a
    person approves. Nothing happens until they do."""
    meeting = await _mine(user, meeting_id)
    item = await _item(meeting, item_id)
    try:
        await follow_ups.propose(user_id=user.id, meeting=meeting, item=item)
    except follow_ups.FollowUpError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _record(meeting)


@router.post("/{meeting_id}/actions/{item_id}/settle", response_model=MeetingRecord)
async def settle_action(
    meeting_id: str,
    item_id: int,
    body: SettleItemRequest,
    user: UserModel = Depends(get_user),
) -> MeetingRecord:
    """Confirm, decline or undo one action's card. Confirm names the version
    the person saw; a changed card is refused."""
    meeting = await _mine(user, meeting_id)
    item = await _item(meeting, item_id)
    try:
        await follow_ups.settle(
            user_id=user.id,
            meeting=meeting,
            item=item,
            verb=body.verb,
            version=body.version,
        )
    except follow_ups.FollowUpError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _record(meeting)
