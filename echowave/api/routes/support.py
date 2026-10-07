"""Help: a person's support requests (launch stream `support`, screen 28).

Thin: each route resolves the signed-in person and their selected workspace
and hands over to ``services/support``. Everything is a 404 while
``support_help`` is off for the workspace. A ticket is its requester's, in
the workspace it was opened from; nothing here reads staff internal notes.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Response,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.support import attachments, sharing, tickets

router = APIRouter(
    prefix="/help",
    tags=["support"],
    dependencies=[Depends(features.require("support_help", per_organization=True))],
)


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


class Category(BaseModel):
    key: str
    label: str


class HelpOptions(BaseModel):
    categories: list[Category]
    not_shared: str
    max_attachment_bytes: int
    attachment_types: list[str]


@router.get("/options", response_model=HelpOptions)
async def help_options() -> HelpOptions:
    return HelpOptions(
        categories=[Category(key=k, label=v) for k, v in tickets.CATEGORIES.items()],
        not_shared=sharing.NOT_SHARED_NOTE,
        max_attachment_bytes=attachments.MAX_BYTES,
        attachment_types=sorted(attachments.CONTENT_TYPES),
    )


class ShareField(BaseModel):
    label: str
    value: Any = None


class ShareSection(BaseModel):
    key: str
    label: str
    included: bool
    required: bool
    fields: list[ShareField]


class Affected(BaseModel):
    kind: Literal["task", "reply"]
    id: int


class SharePreview(BaseModel):
    affected: Affected | None = None
    sections: list[ShareSection]
    not_shared: str


class SharePreviewRequest(BaseModel):
    affected_kind: Literal["task", "reply"] | None = None
    affected_id: int | None = None
    #: The optional sections switched on; omitted means the defaults.
    share: list[str] | None = None

    model_config = ConfigDict(extra="forbid")


@router.post("/share-preview", response_model=SharePreview)
async def share_preview(
    body: SharePreviewRequest, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    """Exactly what support will see if the request is sent like this."""
    try:
        return await sharing.build(
            organization_id=_organization_id(user),
            user_id=user.id,
            affected_kind=body.affected_kind,
            affected_id=body.affected_id,
            share=body.share,
        )
    except sharing.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except sharing.ShareRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class TicketCreate(SharePreviewRequest):
    category: str = Field(max_length=24)
    description: str = Field(min_length=1, max_length=tickets.BODY_LIMIT)
    subject: str | None = Field(default=None, max_length=200)


class TicketSummary(BaseModel):
    id: int
    category: str
    category_label: str
    subject: str
    status: str
    affected: Affected | None = None
    reopened_count: int
    created_at: str | None = None
    updated_at: str | None = None
    resolved_at: str | None = None
    support_replied: bool = False


class TicketMessage(BaseModel):
    id: int
    author_kind: str
    body: str
    created_at: str | None = None


class TicketAttachment(BaseModel):
    id: int
    file_name: str
    content_type: str
    size_bytes: int
    created_at: str | None = None


class TicketAction(BaseModel):
    id: int
    summary: str | None = None
    state: str
    finished_at: str | None = None


class SharedSection(BaseModel):
    key: str
    label: str
    fields: list[ShareField]


class SharedSnapshot(BaseModel):
    affected: Affected | None = None
    sections: list[SharedSection] = []
    left_out: list[str] = []
    shared_at: str | None = None


class TicketDetail(TicketSummary):
    shared: SharedSnapshot
    messages: list[TicketMessage]
    attachments: list[TicketAttachment]
    actions: list[TicketAction]


class TicketCreated(BaseModel):
    ticket: TicketSummary
    created: bool


@router.post("/tickets", response_model=TicketCreated, status_code=201)
async def create_ticket(
    body: TicketCreate,
    response: Response,
    user: Annotated[UserModel, Depends(get_user)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    try:
        ticket, created = await tickets.create(
            organization_id=_organization_id(user),
            user_id=user.id,
            category=body.category,
            description=body.description,
            subject=body.subject,
            affected_kind=body.affected_kind,
            affected_id=body.affected_id,
            share=body.share,
            idempotency_key=idempotency_key,
        )
    except sharing.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (sharing.ShareRefused, tickets.TicketError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not created:
        response.status_code = 200
    return {"ticket": ticket, "created": created}


@router.get("/tickets", response_model=list[TicketSummary])
async def my_tickets(
    user: Annotated[UserModel, Depends(get_user)],
) -> list[dict[str, Any]]:
    return await tickets.list_mine(_organization_id(user), user.id)


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
async def my_ticket(
    ticket_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    try:
        return await tickets.get_mine(_organization_id(user), user.id, ticket_id)
    except tickets.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ReplyWrite(BaseModel):
    body: str = Field(min_length=1, max_length=tickets.BODY_LIMIT)
    #: The client's key for this message: a retry with it is one message.
    client_key: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")


@router.post(
    "/tickets/{ticket_id}/messages", response_model=TicketMessage, status_code=201
)
async def reply(
    ticket_id: int, body: ReplyWrite, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    try:
        return await tickets.reply_as_customer(
            organization_id=_organization_id(user),
            user_id=user.id,
            ticket_id=ticket_id,
            body=body.body,
            client_key=body.client_key,
        )
    except tickets.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except tickets.TicketError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tickets/{ticket_id}/resolve", response_model=TicketDetail)
async def resolve(
    ticket_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    try:
        return await tickets.resolve_as_customer(
            organization_id=_organization_id(user), user_id=user.id, ticket_id=ticket_id
        )
    except tickets.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ReopenWrite(BaseModel):
    body: str | None = Field(default=None, max_length=tickets.BODY_LIMIT)

    model_config = ConfigDict(extra="forbid")


@router.post("/tickets/{ticket_id}/reopen", response_model=TicketDetail)
async def reopen(
    ticket_id: int, body: ReopenWrite, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    try:
        return await tickets.reopen_as_customer(
            organization_id=_organization_id(user),
            user_id=user.id,
            ticket_id=ticket_id,
            body=body.body,
        )
    except tickets.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except tickets.TicketError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/tickets/{ticket_id}/attachments", response_model=TicketAttachment, status_code=201
)
async def attach(
    ticket_id: int,
    user: Annotated[UserModel, Depends(get_user)],
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    data = await file.read(attachments.MAX_BYTES + 1)
    try:
        return await attachments.add(
            organization_id=_organization_id(user),
            user_id=user.id,
            ticket_id=ticket_id,
            file_name=file.filename or "file",
            content_type=file.content_type or "application/octet-stream",
            data=data,
        )
    except attachments.StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except attachments.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except attachments.AttachmentRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
