"""Images over HTTP: the provider card, the images on it, the logo upload.

Thin: every route resolves the caller's workspace from the session -- never
from the request -- and hands over to ``services/images``. An image id from
another workspace is answered as not found. A key goes in and never comes
back out: responses carry its last four characters at most.

All a 404 while ``image_generation`` is off for the workspace.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services import features
from api.services.auth.depends import get_user, require_organization_role
from api.services.images import keys, service, store

router = APIRouter(
    prefix="/images",
    tags=["images"],
    dependencies=[Depends(features.require("image_generation", per_organization=True))],
)


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


class ImageProviderState(BaseModel):
    provider: str
    label: str
    blurb: str
    key_label: str
    key_hint: str
    ready: bool
    #: ``your_key``, ``platform``, or None when it needs a key.
    source: str | None = None
    masked_key: str | None = None
    takes_references: bool


class ImageProvidersResponse(BaseModel):
    chosen: str | None = None
    ready: bool
    providers: list[ImageProviderState]
    encryption_configured: bool
    verification: str | None = None
    verification_message: str | None = None


class ConnectImageProviderRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=32)
    #: Empty to use a key the workspace already has, or the platform's.
    api_key: str | None = Field(default=None, max_length=4096)
    verify: bool = True


class ImageView(BaseModel):
    image_uuid: str
    kind: str
    provider: str
    format: str
    width: int | None = None
    height: int | None = None
    mime_type: str
    size_bytes: int
    filename: str | None = None
    option_index: int
    parent_uuid: str | None = None
    created_at: str | None = None


class ImageUrlResponse(BaseModel):
    image: ImageView
    url: str
    expires_in: int


@router.get("/providers", response_model=ImageProvidersResponse)
async def image_providers(user: UserModel = Depends(get_user)):
    """The three providers, which are ready and on whose key, and the choice."""
    return await keys.status(_organization_id(user))


@router.put("/provider", response_model=ImageProvidersResponse)
async def connect_image_provider(
    body: ConnectImageProviderRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
):
    """Choose the provider, storing its key first when one is given.

    Admins only, as with every key in the vault. A key the vendor refuses is
    not stored and the vendor's refusal is said; one we could not check is
    stored and reported as unverified."""
    try:
        return await keys.connect(
            _organization_id(user),
            user_id=user.id,
            provider=body.provider,
            api_key=body.api_key,
            verify=body.verify,
        )
    except keys.ImageKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/references", response_model=ImageView)
async def upload_reference_image(
    file: Annotated[UploadFile, File()],
    user: UserModel = Depends(get_user),
):
    """A logo or product photo to put on a poster. Returns its image id,
    which the composer sends as the message's attachment."""
    data = await file.read(store.MAX_REFERENCE_BYTES + 1)
    try:
        return await service.add_reference(
            organization_id=_organization_id(user),
            user_id=user.id,
            data=data,
            filename=file.filename,
        )
    except service.ReferenceRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=503, detail="The image could not be saved. Try again."
        ) from exc


async def _row(user: UserModel, image_uuid: str):
    row = await store.get(_organization_id(user), image_uuid)
    if row is None:
        raise HTTPException(status_code=404, detail="No such image here")
    return row


@router.get("/{image_uuid}", response_model=ImageUrlResponse)
async def image_url(image_uuid: str, user: UserModel = Depends(get_user)):
    """A short-lived signed URL to show one of this workspace's images."""
    row = await _row(user, image_uuid)
    url = await store.signed_url(row)
    if not url:
        raise HTTPException(status_code=503, detail="The image could not be fetched")
    return {"image": store.view(row), "url": url, "expires_in": store.URL_SECONDS}


@router.get(
    "/{image_uuid}/file",
    response_class=Response,
    responses={200: {"content": {"image/png": {}, "image/jpeg": {}, "image/webp": {}}}},
)
async def download_image(image_uuid: str, user: UserModel = Depends(get_user)):
    """The image itself, as a download, through the session's workspace."""
    row = await _row(user, image_uuid)
    data = await store.read_bytes(row)
    if not data:
        raise HTTPException(status_code=404, detail="The image file is missing")
    extension = store.REFERENCE_TYPES.get(row.mime_type, "png")
    name = f"{row.format or 'image'}-{row.option_index + 1}-{row.image_uuid[4:12]}"
    return Response(
        content=data,
        media_type=row.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{name}.{extension}"'},
    )
