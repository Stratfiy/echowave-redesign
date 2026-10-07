"""Reading back the timeline every screen was supposed to be built on.

``services/workflow/agent_timeline.py`` has been writing these rows since it
shipped. Nothing has ever read them. A write-only table is the same silent
absence this codebase keeps relearning: the data was there the whole time, the
screens said "no activity", and nobody could tell the difference between an
agent that did nothing and an agent whose work was never surfaced.

Three things this route is careful about, all of them consequences of what the
rows contain rather than of REST taste:

**The organisation is not a filter, it is the query.** Every read is scoped to
``user.selected_organization_id`` before any request-supplied id is looked at.
An id in a query string proves nothing.

**An id that is not yours is 404, not empty.** Passing ``workflow_id`` for
another tenant's bot returns "not found" rather than an empty list, because an
empty list is indistinguishable from "that bot has been quiet" and would let
somebody enumerate which ids exist.

**``ON_REQUEST`` stays off unless asked for, per call.** Recordings,
transcripts and caller words sit behind ``include_transcripts``. It is a
deliberate act by the reader, never a default, and ``OFF`` is returned by
nothing at all -- the db layer drops it whatever this route passes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from api import constants
from api.db import db_client
from api.db.models import UserModel
from api.enums import (
    ORGANIZATION_ROLE_RANK,
    AgentEventActor,
    AgentEventKind,
    OrganizationRole,
)
from api.services.auth.depends import get_user, require_organization_role
from api.services.configuration import chat_presets
from api.services.workflow import (
    actions,
    agent_timeline,
    blocked,
    chat_memory,
    decibyl,
    decisions,
    home_openers,
    mentions,
    reply_draft,
    secrets_request,
    self_edit,
    visibility,
)
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

router = APIRouter(prefix="/timeline", tags=["agent-timeline"])


class BlockedWay(BaseModel):
    """One lettered choice on a wall card."""

    letter: str
    label: str
    #: A route inside this product. Never an external link, and never a
    #: screen without the control it promises -- see blocked.py.
    href: str


class BlockedWall(BaseModel):
    """What stopped the bot, and the ways past it."""

    reason: str
    says: str
    ways: list[BlockedWay] = []


class TimelineEvent(BaseModel):
    id: int
    at: datetime
    #: Plain strings rather than enums, matching how the column is stored. A
    #: kind written by a newer deploy must render as itself on an older
    #: screen rather than fail validation and blank the whole feed.
    kind: str
    actor: str
    summary: str
    payload: dict[str, Any]
    is_deliverable: bool
    workflow_id: Optional[int]
    workflow_run_id: Optional[int]
    folder_id: Optional[int]
    #: What this row cost in credits, and whether it is included in the plan
    #: rather than charged (KAN-56). Every kind is one or the other.
    credits: int = 0
    included: bool = True
    #: For a row where something stopped the bot: what the wall was, and the
    #: lettered ways past it. Null on every other kind, so a screen can
    #: branch on its presence rather than on a list of kinds it has to keep
    #: in step with the server. Computed at read time -- see
    #: services/workflow/blocked.py.
    blocked: Optional[BlockedWall] = None


class TimelineResponse(BaseModel):
    events: list[TimelineEvent]
    #: The cursor for the next page, or null when this page is the end. Null
    #: rather than omitted so a client can branch on it without guessing.
    #:
    #: A PAIR, because the rows are ordered by (at, id) and a cursor that
    #: compares less than the sort does silently drops rows -- see the note in
    #: db/agent_event_client.py. Both halves are passed back together or
    #: neither is.
    next_before_at: Optional[datetime]
    next_before_id: Optional[int]
    #: True when this page hit the limit and more rows exist that this
    #: response gives no way to ask for. Only reachable on the single-call
    #: path, which is deliberately uncursored; everywhere else `next_before_id`
    #: is the answer. Said out loud rather than left as a short list, because
    #: a truncated history that looks complete is the failure this whole
    #: module exists to stop.
    truncated: bool = False


def _as_event(row: Any) -> TimelineEvent:
    from api.services.billing import events as billing_events

    price = billing_events.timeline_price(row.kind, row.payload or {})
    return TimelineEvent(
        id=row.id,
        at=row.at,
        kind=row.kind,
        actor=row.actor,
        summary=row.summary,
        payload=row.payload or {},
        is_deliverable=bool(row.is_deliverable),
        workflow_id=row.workflow_id,
        workflow_run_id=row.workflow_run_id,
        folder_id=row.folder_id,
        credits=price["credits"],
        included=price["included"],
        blocked=(
            BlockedWall(**wall.as_dict())
            if (
                wall := blocked.classify(
                    row.kind, row.payload or {}, workflow_id=row.workflow_id
                )
            )
            else None
        ),
    )


@router.get("", response_model=TimelineResponse)
async def timeline(
    workflow_id: Annotated[Optional[int], Query()] = None,
    workflow_run_id: Annotated[Optional[int], Query()] = None,
    folder_id: Annotated[Optional[int], Query()] = None,
    assistant: Annotated[bool, Query()] = False,
    #: Which of Decibyl's conversations, with ``assistant``. Omitted is the
    #: thread the account has always had, so a client that knows nothing
    #: about threads reads what it always read.
    thread_id: Annotated[Optional[str], Query(max_length=36)] = None,
    kinds: Annotated[Optional[list[str]], Query()] = None,
    deliverables_only: Annotated[bool, Query()] = False,
    include_transcripts: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before_at: Annotated[Optional[datetime], Query()] = None,
    before_id: Annotated[Optional[int], Query()] = None,
    user: UserModel = Depends(get_user),
) -> TimelineResponse:
    """One feed, four screens.

    No filter gives the account's whole history; ``workflow_id`` gives one
    bot's; ``workflow_run_id`` gives one call, and that one reads *forwards*
    because a call is a story. ``deliverables_only`` gives the Deliverables
    list. The db layer owns that ordering rule -- see ``agent_events``.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Ownership before reading, for each id the caller supplied. Checked here
    # rather than trusted to the org filter on agent_events because a row that
    # was never written is indistinguishable from a row that was filtered out,
    # and "your colleague's bot has no history" is a different sentence from
    # "that bot is not yours".
    if workflow_id is not None:
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

    if workflow_run_id is not None:
        run = await db_client.get_workflow_run(
            workflow_run_id, organization_id=organization_id
        )
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

    if folder_id is not None:
        folder = await db_client.get_folder(folder_id, organization_id=organization_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

    if assistant:
        await _assert_thread_is_theirs(user, organization_id, thread_id)

    rows = await db_client.agent_events(
        organization_id=organization_id,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        folder_id=folder_id,
        kinds=(kinds or None)
        if not assistant
        else decibyl.thread_filter(organization_id)["kinds"],
        assistant_thread=assistant,
        thread_id=thread_id if assistant else None,
        deliverables_only=deliverables_only,
        include_on_request=include_transcripts,
        limit=limit,
        before_at=before_at,
        before_id=before_id,
        # A person's own reach rows (connect chips, comparisons, order
        # cards) are theirs alone, whatever thread they sit on.
        viewer_id=user.id,
    )

    events = [_as_event(row) for row in rows]

    # A cursor only where paging exists. One call is returned whole and in
    # ascending order, so handing back its last id would page a client
    # backwards through a story it is reading forwards.
    next_before_at = None
    next_before_id = None
    truncated = False
    full_page = bool(events) and len(events) == limit
    if workflow_run_id is None:
        if full_page:
            next_before_at = events[-1].at
            next_before_id = events[-1].id
    elif full_page:
        # One call is returned whole and ascending, so there is no cursor to
        # hand back -- but a call long enough to fill the page would otherwise
        # end mid-story with nothing saying so.
        truncated = True

    return TimelineResponse(
        events=events,
        next_before_at=next_before_at,
        next_before_id=next_before_id,
        truncated=truncated,
    )


#: What one message may carry. Long enough for anything a person types into a
#: channel and short enough that a paste of a whole document is refused rather
#: than silently stored and truncated somewhere downstream.
MAX_MESSAGE = 8000


class Attachment(BaseModel):
    """A file dropped into the channel, already uploaded and being read."""

    document_uuid: str = Field(max_length=64)
    filename: str = Field(max_length=500)
    size_bytes: int = Field(default=0, ge=0)


class PostMessageRequest(BaseModel):
    #: Where it is said: a channel, one bot's own chat, or Decibyl's thread.
    #: Exactly one.
    folder_id: Optional[int] = None
    workflow_id: Optional[int] = None
    assistant: bool = False
    #: Empty is allowed only alongside an attachment: a file is a message.
    text: str = Field(default="", max_length=MAX_MESSAGE)
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    #: The brain for this message: a chat preset slug (everyday, smart, deep)
    #: or a catalogue model (``model:openai/gpt-5``), or nothing for the
    #: bot's own. See chat_presets.
    preset: Optional[str] = Field(default=None, max_length=96)
    #: With ``assistant``: which of Decibyl's conversations this is said in.
    #: Any id the client mints; the thread exists from its first message.
    #: Null is the original thread.
    thread_id: Optional[str] = Field(default=None, max_length=36)


class PostMessageResponse(BaseModel):
    #: Bots this message was handed to. Empty is an ordinary outcome: a person
    #: talking to their colleagues in a channel has addressed nobody, and that
    #: is not a failure.
    asked: list[int]
    #: Handles that matched no bot in this channel. Returned so the screen can
    #: say "there is nobody here called @op-bot" rather than leaving somebody
    #: waiting on a reply that was never going to come.
    unknown: list[str]
    #: Handles that matched more than one bot here. Answering with whichever
    #: came back first would hide a naming collision behind a bot that
    #: sometimes replies and sometimes does not.
    ambiguous: list[str]


class BrainModel(BaseModel):
    slug: str
    label: str
    #: What a reply on this model is expected to cost, in credits. Only
    #: under the charge rule (D-1); absent otherwise.
    reply_credits: Optional[int] = None


class BrainVendor(BaseModel):
    id: str
    label: str
    models: list[BrainModel]


class BrainPreset(BaseModel):
    slug: str
    label: str
    blurb: str
    #: As on ``BrainModel``.
    reply_credits: Optional[int] = None


class BrainsResponse(BaseModel):
    #: The three words on the menu.
    presets: list[BrainPreset]
    #: "More models", by vendor: only vendors the platform holds a key for.
    vendors: list[BrainVendor]


@router.get("/brains", response_model=BrainsResponse)
async def brains(
    assistant: Annotated[bool, Query()] = False,
    user: UserModel = Depends(get_user),
) -> BrainsResponse:
    """What the composer's picker offers.

    Decibyl's own thread runs on the builder client, which drives three
    vendors; a bot's chat runs on the pipeline, which drives every vendor
    in the catalogue. ``assistant`` says which menu.
    """
    vendors: Optional[list[str]] = None
    if assistant:
        from api.services.agent_builder.client import SUPPORTED_PROVIDERS

        vendors = list(SUPPORTED_PROVIDERS)
    async with db_client.async_session() as session:
        menu = await chat_presets.menu(session, vendors=vendors)
    return BrainsResponse(**menu)


class ChatMemoryResponse(BaseModel):
    #: Tokens of the thread inside the window, and the window.
    used_tokens: int
    budget_tokens: int
    messages_kept: int
    messages_total: int
    plan_code: str
    #: The plan with a bigger memory, or null when none has.
    raise_to: Optional[str]


@router.get("/memory", response_model=ChatMemoryResponse)
async def memory(
    workflow_id: Annotated[Optional[int], Query()] = None,
    folder_id: Annotated[Optional[int], Query()] = None,
    assistant: Annotated[bool, Query()] = False,
    thread_id: Annotated[Optional[str], Query(max_length=36)] = None,
    user: UserModel = Depends(get_user),
) -> ChatMemoryResponse:
    """The meter beside the composer: what this chat keeps in mind, out of
    what the plan allows. The same arithmetic the reply uses."""
    organization_id = user.selected_organization_id
    if workflow_id is not None:
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="No such agent here")
    if folder_id is not None:
        folder = await db_client.get_folder(folder_id, organization_id=organization_id)
        if folder is None:
            raise HTTPException(status_code=404, detail="No such channel here")
    usage = await chat_memory.usage(
        organization_id,
        workflow_id=workflow_id,
        folder_id=folder_id,
        assistant=assistant,
        thread_id=thread_id if assistant else None,
    )
    return ChatMemoryResponse(**usage.as_dict())


class ThreadSummary(BaseModel):
    #: Null for the conversation the account has always had -- the rows
    #: written before threads existed. It is a thread like any other here.
    thread_id: Optional[str]
    #: The first thing said in it, which is what somebody recognises a chat
    #: by. Empty only for a thread whose opening line was somehow blank.
    title: str
    last_at: datetime
    messages: int


class ThreadsResponse(BaseModel):
    threads: list[ThreadSummary]


async def _is_admin(user: UserModel, organization_id: int) -> bool:
    membership = await db_client.get_membership(user.id, organization_id)
    rank = ORGANIZATION_ROLE_RANK.get(membership.role if membership else "", -1)
    return rank >= ORGANIZATION_ROLE_RANK[OrganizationRole.ADMIN.value]


async def _assert_thread_is_theirs(
    user: UserModel, organization_id: int, thread_id: Optional[str]
) -> None:
    """A Decibyl conversation is its author's (D-1b). A thread that is not
    theirs is answered as not found, the way a wrong tenant is: a 403 would
    confirm the id names somebody else's chat."""
    from api.services.meetings import is_meeting_thread

    if is_meeting_thread(thread_id):
        # A meeting's follow-up cards are read from the meeting record, by
        # the person who captured it -- never as a chat, by anyone.
        raise HTTPException(status_code=404, detail="Thread not found")
    if not constants.DECIBYL_PRIVATE_THREADS_ENABLED:
        return
    author = await db_client.thread_author(
        organization_id=organization_id, thread_id=thread_id
    )
    if author == user.id:
        return
    if author is None and await _is_admin(user, organization_id):
        return
    if author is None and thread_id is not None:
        # Nobody has spoken in it yet: a new chat the client just minted.
        first = await db_client.agent_events(
            organization_id=organization_id,
            assistant_thread=True,
            thread_id=thread_id,
            limit=1,
        )
        if not first:
            return
    raise HTTPException(status_code=404, detail="Thread not found")


@router.get("/threads", response_model=ThreadsResponse)
async def threads(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    user: UserModel = Depends(get_user),
) -> ThreadsResponse:
    """Decibyl's conversations, for the list under the composer.

    There is no matching create call, and that is the design rather than a
    gap: a thread is the id its events share, so a new chat is a new id the
    client mints and starts writing to. An endpoint that recorded one before
    anything was said would leave empty chats in this list every time
    somebody pressed New and changed their mind.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    private = constants.DECIBYL_PRIVATE_THREADS_ENABLED
    rows = await db_client.assistant_threads(
        organization_id=organization_id,
        limit=limit,
        viewer_id=user.id if private else None,
        viewer_is_admin=await _is_admin(user, organization_id) if private else False,
    )
    return ThreadsResponse(threads=[ThreadSummary(**row) for row in rows])


@router.get("/recents")
async def recents(
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
    user: UserModel = Depends(get_user),
) -> dict:
    """The rail's Recents: Decibyl chats and agent chats, newest first."""
    from api.services.workflow import recents as recents_service

    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    private = constants.DECIBYL_PRIVATE_THREADS_ENABLED
    items = await recents_service.recents(
        organization_id=organization_id,
        viewer_id=user.id if private else None,
        viewer_is_admin=await _is_admin(user, organization_id) if private else False,
        limit=limit,
    )
    return {"items": items}


@router.post("/message", response_model=PostMessageResponse)
async def post_message(
    body: PostMessageRequest,
    user: UserModel = Depends(get_user),
) -> PostMessageResponse:
    """Say something in a channel, and hand it to whichever bots it addressed.

    The message is recorded as an ``agent_event`` rather than in a table of its
    own. That is not thrift: a channel is a thread of what happened there, and
    a person asking for something is one of the things that happened. Keeping
    messages beside outcomes, failures and deliverables means the timeline
    reads as a conversation instead of two logs interleaved by a screen -- and
    every reader already has tenancy, the visibility gate and the cursor.

    The reply is enqueued rather than awaited. A bot's turn is an LLM call and
    a person who has just pressed enter should see their own message
    immediately; a request that blocks until a bot has thought is a request
    that times out on the one occasion the model is slow.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    places = sum(1 for p in (body.folder_id, body.workflow_id) if p is not None) + int(
        body.assistant
    )
    if places != 1:
        raise HTTPException(
            status_code=422,
            detail="Say it in a channel, to an agent, or to Decibyl, one of the three",
        )

    if body.assistant:
        await _assert_thread_is_theirs(user, organization_id, body.thread_id)
        text, attachments, line, preset = await _what_was_said(body, organization_id)
        asked = await decibyl.ask(
            organization_id=organization_id,
            user_id=user.id,
            text=text,
            attachments=attachments,
            line=line,
            preset=preset,
            thread_id=body.thread_id,
        )
        return PostMessageResponse(asked=asked, unknown=[], ambiguous=[])

    if body.workflow_id is not None:
        if await decibyl.turn_refused(
            organization_id=organization_id,
            user_id=user.id,
            workflow_id=body.workflow_id,
        ):
            return PostMessageResponse(asked=[], unknown=[], ambiguous=[])
        return await _post_direct_message(
            organization_id=organization_id, user=user, body=body
        )

    folder = await db_client.get_folder(body.folder_id, organization_id=organization_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Channel not found")

    # The roster is the bots in this channel, not every bot the account owns.
    # A bot that is not here cannot be addressed here, the same rule Slack
    # applies to apps -- and it is what makes putting a bot in a channel mean
    # something rather than being decoration.
    workflows = visibility.only_visible(
        await db_client.get_all_workflows_for_listing(organization_id=organization_id),
        await visibility.role_of(getattr(user, "id", None), organization_id),
    )
    roster = [
        {
            "id": workflow.id,
            "handle": getattr(workflow, "handle", None),
            # Carried alongside the handle for the fallback in `resolve`: a bot
            # whose handle never got assigned is still addressable by the one
            # its name implies, rather than silently addressable by nothing.
            "name": workflow.name,
        }
        for workflow in workflows
        if getattr(workflow, "folder_id", None) == body.folder_id
    ]
    text, attachments, line, preset = await _what_was_said(body, organization_id)

    resolution = mentions.resolve(text, roster)
    if resolution.mentioned and await decibyl.turn_refused(
        organization_id=organization_id, user_id=user.id, folder_id=body.folder_id
    ):
        # Asking a bot in a channel is a turn too; a line that addresses no
        # bot asks no model and is not counted.
        return PostMessageResponse(asked=[], unknown=[], ambiguous=[])

    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        # The summary is the display line and truncates at 500; the words the
        # person actually chose are kept whole in the payload. A message
        # silently cut short is the product editing somebody.
        summary=line,
        folder_id=body.folder_id,
        payload={
            "body": text,
            "author_id": user.id,
            "asked": [m.workflow_id for m in resolution.mentioned],
            "attachments": attachments,
            "preset": preset,
        },
    )

    for mention in resolution.mentioned:
        try:
            await enqueue_job(
                FunctionNames.ANSWER_CHANNEL_MESSAGE,
                mention.workflow_id,
                body.folder_id,
                line,
                preset,
            )
        except Exception as exc:  # noqa: BLE001 - one bot failing is not all of them
            # Said out loud, because the alternative is a bot that was
            # addressed, never answered, and left no trace of having been
            # asked -- which reads to the person as being ignored.
            logger.error(
                "Could not ask workflow {} to answer in channel {}: {}",
                mention.workflow_id,
                body.folder_id,
                exc,
            )
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.COULD_NOT.value,
                summary=f"@{mention.handle} could not be reached to answer that",
                workflow_id=mention.workflow_id,
                folder_id=body.folder_id,
            )

    return PostMessageResponse(
        asked=[m.workflow_id for m in resolution.mentioned],
        unknown=resolution.unknown,
        ambiguous=resolution.ambiguous,
    )


class DecideRequest(BaseModel):
    event_id: int
    #: The options picked, by their text. One for single and approve modes.
    choice: list[str] = Field(default_factory=list, max_length=decisions.MAX_OPTIONS)
    #: A written answer, where the question allows one.
    other: Optional[str] = Field(default=None, max_length=decisions.MAX_OPTION_CHARS)


async def _what_was_said(
    body: PostMessageRequest, organization_id: int
) -> tuple[str, list[dict[str, Any]], str, Optional[str]]:
    """The text, the checked attachments, the display line, and the brain.

    An attachment names a document; the document has to be this
    organisation's, and a uuid from another tenant is refused rather than
    silently dropped -- a file card that points at nothing is a lie on the
    screen. Empty text is allowed only beside an attachment: a file is a
    message.
    """
    text = body.text.strip()
    if not text and not body.attachments:
        raise HTTPException(status_code=422, detail="Say something or attach a file")
    try:
        preset = chat_presets.normalise(body.preset)
    except chat_presets.UnknownPreset as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    attachments: list[dict[str, Any]] = []
    for attachment in body.attachments:
        document = await db_client.get_document_by_uuid(
            attachment.document_uuid, organization_id=organization_id
        )
        if document is None:
            raise HTTPException(status_code=404, detail="No such document here")
        attachments.append(
            {
                "document_uuid": attachment.document_uuid,
                "filename": attachment.filename or document.filename,
                "size_bytes": attachment.size_bytes or (document.file_size_bytes or 0),
            }
        )

    # The display line: what was typed, or the file's name when nothing was.
    line = text or "Shared " + ", ".join(a["filename"] for a in attachments)
    return text, attachments, line, preset


async def _post_direct_message(
    *, organization_id: int, user: UserModel, body: PostMessageRequest
) -> PostMessageResponse:
    """Talk to one bot on its own chat.

    No handle to resolve -- the bot is the one whose chat this is -- and
    nothing here is filed in the channel the bot sits in: ``in_channel=False``
    on both the question and, in the reply path, the answer. The bot's own
    thread shows both, which is what the person is looking at.
    """
    workflow = await db_client.get_workflow(
        body.workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="No such agent")
    text, attachments, line, preset = await _what_was_said(body, organization_id)

    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary=line,
        workflow_id=workflow.id,
        payload={
            "body": text,
            "author_id": user.id,
            "asked": [workflow.id],
            "direct": True,
            "attachments": attachments,
            "preset": preset,
        },
        in_channel=False,
    )
    try:
        await enqueue_job(
            FunctionNames.ANSWER_CHANNEL_MESSAGE, workflow.id, None, line, preset
        )
    except Exception as exc:  # noqa: BLE001 - said out loud, as in the channel path
        logger.error(
            "Could not ask workflow {} to answer directly: {}", workflow.id, exc
        )
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.COULD_NOT.value,
            summary=f"{workflow.name} could not be reached to answer that",
            workflow_id=workflow.id,
            in_channel=False,
        )
    return PostMessageResponse(asked=[workflow.id], unknown=[], ambiguous=[])


class ThreadChip(BaseModel):
    """One tap that continues the conversation.

    Pressed, the text is sent to Decibyl as an ordinary message -- the same
    thing the home screen's opener cards do. Nothing here is a command or a
    shortcut into a screen: a chip a person cannot also type is a chip that
    teaches them nothing about how to ask next time.
    """

    kind: str
    text: str


class ThreadChipsResponse(BaseModel):
    chips: list[ThreadChip]


@router.get("/chips", response_model=ThreadChipsResponse)
async def thread_chips(
    user: UserModel = Depends(get_user),
) -> ThreadChipsResponse:
    """What to offer under the last reply, so the thread carries its own
    next steps.

    A person who has just been told something has a next question, and
    until now they had to compose it. The home screen has had these since
    it was built (``home_openers``) -- this is the same cards, in the place
    they matter most, because the moment somebody wants to keep going is
    the moment they have just read an answer rather than the moment they
    arrived.

    Built from this account's own life, never invented: what they asked
    before, the busiest bot by name, callers nobody rang back, a stuck task,
    the time of day. A brand-new account gets the first jobs for the
    business it named at the door instead of five generic prompts.

    Never fails the thread. A screen with no chips is a screen exactly as
    useful as it was yesterday; a screen that will not load because the
    chips could not be built is worse than the feature not existing.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    try:
        from api.routes.team import _members

        members = [m.model_dump() for m in await _members(organization_id, 24)]
        missed = await db_client.unreturned_missed_call_count(organization_id, hours=48)
        cards = await home_openers.gather(
            organization_id, members=members, unreturned_missed_calls=missed
        )
    except Exception as exc:  # noqa: BLE001 - the thread still works
        logger.warning("Could not build thread chips for {}: {}", organization_id, exc)
        return ThreadChipsResponse(chips=[])

    return ThreadChipsResponse(
        chips=[
            ThreadChip(kind=str(c.get("kind") or "suggestion"), text=str(c["text"]))
            for c in cards
            if c.get("text")
        ]
    )


@router.post("/decide", response_model=TimelineEvent)
async def decide(body: DecideRequest, user: UserModel = Depends(get_user)):
    """Answer a bot's question on the card that asked it.

    The answer is written into the question's own row, so the card everyone
    sees shows what was decided and by whom; it is also posted as a message in
    the channel and handed to the bot, which carries on with it. A question
    already answered stays answered: the bot has acted on the first answer.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    try:
        await decisions.decide(
            organization_id=organization_id,
            event_id=body.event_id,
            choice=body.choice,
            other=body.other,
            user_id=user.id,
        )
    except decisions.DecisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row = await db_client.get_agent_event(
        body.event_id, organization_id=organization_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="That question is not here")
    return _as_event(row)


class DraftResponse(BaseModel):
    #: The reply so far, or empty when nothing is forming.
    text: str


@router.get("/draft", response_model=DraftResponse)
async def reply_draft_text(
    workflow_id: Annotated[Optional[int], Query()] = None,
    thread_id: Annotated[Optional[str], Query(max_length=64)] = None,
    user: UserModel = Depends(get_user),
) -> DraftResponse:
    """The answer as it forms, for the thinking row. Decibyl's thread with
    no ``workflow_id``; a bot's own chat with one. Empty is the ordinary
    answer between replies. See services/workflow/reply_draft.py."""
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return DraftResponse(
        text=await reply_draft.get(
            organization_id, workflow_id=workflow_id, thread_id=thread_id
        )
    )


class SettleActionRequest(BaseModel):
    event_id: int
    #: confirm | decline | undo. See services/workflow/actions.py.
    verb: str = Field(max_length=16)
    #: The card's payload version the person was shown. With the task
    #: ledger on, Confirm approves exactly that version and no other.
    version: Optional[str] = Field(default=None, max_length=32)


@router.post("/actions/settle", response_model=TimelineEvent)
async def settle_action(body: SettleActionRequest, user: UserModel = Depends(get_user)):
    """Confirm, decline or undo a proposed action on the card that proposed it.

    Confirm arms it and it fires after a short undo window; undo inside that
    window cancels it, and after it ran puts it back if it can be. Every
    press is stamped into the proposal's own row, so the card is the record.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    try:
        await actions.settle(
            organization_id=organization_id,
            event_id=body.event_id,
            verb=body.verb,
            user_id=user.id,
            version=body.version,
        )
    except actions.ActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row = await db_client.get_agent_event(
        body.event_id, organization_id=organization_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="That proposal is not here")
    return _as_event(row)


class ReviseActionRequest(BaseModel):
    event_id: int
    #: The new arguments: a connected app's call arguments, or a document
    #: card's note, recipient and channel.
    arguments: dict[str, Any]


@router.post("/actions/revise", response_model=TimelineEvent)
async def revise_action(body: ReviseActionRequest, user: UserModel = Depends(get_user)):
    """Edit what a waiting card will do (task ledger). The edit is a new
    version; any Confirm given before it no longer stands."""
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    from api.services.workflow import task_ledger

    if not task_ledger.enabled(organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        await actions.revise(
            organization_id=organization_id,
            event_id=body.event_id,
            arguments=body.arguments,
            user_id=user.id,
        )
    except actions.ActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row = await db_client.get_agent_event(
        body.event_id, organization_id=organization_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="That proposal is not here")
    return _as_event(row)


class ProvideSecretRequest(BaseModel):
    event_id: int
    #: The form's values, keyed by the field keys the card was given. They
    #: travel here once, over TLS, into the credential store, and are not
    #: echoed back in the response or written to the timeline.
    values: dict[str, str] = Field(default_factory=dict)


@router.post("/secrets/provide", response_model=TimelineEvent)
async def provide_secret(
    body: ProvideSecretRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
):
    """Fill a bot's secure form: the key goes to Credentials, the card is stamped.

    Admin only, like creating a credential anywhere else. The returned row
    carries the credential's uuid and a last-four hint, never the value.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    try:
        await secrets_request.provide(
            organization_id=organization_id,
            event_id=body.event_id,
            values=body.values,
            user_id=user.id,
        )
    except secrets_request.SecretError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row = await db_client.get_agent_event(
        body.event_id, organization_id=organization_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="That request is not here")
    return _as_event(row)


class SettleEditRequest(BaseModel):
    event_id: int
    #: "publish" puts the draft live; "discard" throws it away.
    action: str = Field(max_length=16)


@router.post("/edits/settle", response_model=TimelineEvent)
async def settle_edit(body: SettleEditRequest, user: UserModel = Depends(get_user)):
    """Publish or discard a change a bot proposed to itself, from its card.

    The action is written into the card's own row, so everyone who opens the
    thread afterwards sees what was decided and by whom; the draft is
    published or discarded in the same call.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    try:
        await self_edit.settle(
            organization_id=organization_id,
            event_id=body.event_id,
            action=body.action.strip().lower(),
            user_id=user.id,
        )
    except self_edit.EditError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row = await db_client.get_agent_event(
        body.event_id, organization_id=organization_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="That change is not here")
    return _as_event(row)
