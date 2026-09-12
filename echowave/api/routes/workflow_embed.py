"""Embed token endpoints for workflows."""

from datetime import UTC, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from api.constants import BACKEND_API_ENDPOINT, ENVIRONMENT, UI_APP_URL
from api.db import db_client
from api.db.models import EmbedTokenModel, UserModel
from api.enums import PostHogEvent
from api.services import share_links
from api.services.auth.depends import get_user
from api.services.embed_logo import (
    MAX_LOGO_BYTES,
    LogoRejected,
    is_own_logo_key,
    logo_from_settings,
    logo_storage_key,
    merge_logo_into_settings,
    sanitize_client_settings,
    validate_logo,
)
from api.services.posthog_client import capture_event
from api.services.storage import get_current_storage_backend, get_storage

router = APIRouter(prefix="/workflow")


def generate_embed_script(token: EmbedTokenModel) -> str:
    """Generate the embed script for a given token.

    The text-chat flag rides in the script URL rather than the config the
    widget fetches, because the widget reads it while deciding what to build
    and that happens before the fetch returns. Sending it both ways would give
    two answers that can disagree; the URL is the one that arrives in time.
    """
    base_url = str(UI_APP_URL).rstrip("/")
    settings = token.settings or {}

    # Off unless asked for. The widget has supported a typed conversation since
    # it was written, but only ever when someone hand-edited `text=true` into
    # the script tag -- which no screen mentioned and therefore nobody did. The
    # capability was shipped and invisible; this is the switch it never had.
    text_param = "&text=true" if settings.get("enableText") is True else ""

    return f"""<!-- Decibyl Voice Widget -->
<script>
  (function(d, s, id) {{
    var js, fjs = d.getElementsByTagName(s)[0];
    if (d.getElementById(id)) return;
    js = d.createElement(s); js.id = id;
    js.src = '{base_url}/embed/decibyl-widget.js?token={token.token}&environment={ENVIRONMENT}&apiEndpoint={BACKEND_API_ENDPOINT}{text_param}';
    js.async = true;
    fjs.parentNode.insertBefore(js, fjs);
  }}(document, 'script', 'decibyl-widget'));
</script>"""


class EmbedTokenRequest(BaseModel):
    allowed_domains: Optional[list[str]] = None
    settings: Optional[dict] = None
    usage_limit: Optional[int] = None
    expires_in_days: Optional[int] = 30


def _clean_domains(raw: Optional[list[str]]) -> list[str]:
    """The domains to store, or raise saying why none is not an option.

    Required rather than optional, and checked here rather than left to the
    screen: the token this issues is a bearer credential printed into a public
    page, so a token with nothing to check the origin against can be lifted
    onto any site and dial on this organization's balance. ``validate_origin``
    now refuses an empty list, so accepting one would only mint a token that
    never works.

    ``*`` is still storable for someone who genuinely wants that — it just has
    to be typed.
    """
    cleaned = [d.strip().lower() for d in (raw or []) if d and d.strip()]
    if not cleaned:
        raise HTTPException(
            status_code=400,
            detail=(
                "Add at least one domain the widget may run on, for example "
                "example.com. The embed script is visible to anyone who views "
                "your page, so a token with no domain could be copied and used "
                "on another site at your cost."
            ),
        )
    # Order-preserving de-duplication: two spellings of one domain in the list
    # read as a mistake on the screen that shows them back.
    return list(dict.fromkeys(cleaned))


class EmbedTokenResponse(BaseModel):
    id: int
    token: str
    allowed_domains: Optional[list[str]]
    settings: Optional[dict]
    is_active: bool
    usage_count: int
    usage_limit: Optional[int]
    expires_at: Optional[datetime]
    created_at: datetime
    embed_script: str


@router.post("/{workflow_id}/embed-token")
async def create_or_update_embed_token(
    workflow_id: int,
    request: Request,
    embed_request: EmbedTokenRequest,
    user: UserModel = Depends(get_user),
) -> EmbedTokenResponse:
    """
    Create or update an embed token for a workflow.
    Each workflow can have only one active embed token.
    """
    # Verify workflow exists and user has access
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Check if an embed token already exists for this workflow
    existing_tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=False
    )

    allowed_domains = _clean_domains(embed_request.allowed_domains)

    expires_at = None
    if embed_request.expires_in_days:
        expires_at = datetime.now(UTC) + timedelta(days=embed_request.expires_in_days)

    if existing_tokens:
        # Update the existing token (reactivate if needed)
        token = await db_client.update_embed_token(
            existing_tokens[0].id,
            user.selected_organization_id,
            allowed_domains=allowed_domains,
            # `logo` is server-managed. The client may neither set it — which
            # would let it name any object in the bucket — nor drop it by
            # omission, which is what the widget editor was doing on every save.
            settings=sanitize_client_settings(
                embed_request.settings, existing_tokens[0].settings
            ),
            usage_limit=embed_request.usage_limit,
            expires_at=expires_at,
            is_active=True,
        )
    else:
        # Create new token
        token = await db_client.create_embed_token(
            workflow_id=workflow_id,
            organization_id=user.selected_organization_id,
            created_by=user.id,
            allowed_domains=allowed_domains,
            settings=sanitize_client_settings(embed_request.settings, None),
            usage_limit=embed_request.usage_limit,
            expires_at=expires_at,
        )

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.AGENT_EMBEDDED,
        properties={
            "workflow_id": workflow_id,
            "is_new_token": len(existing_tokens) == 0,
            "has_domain_restriction": True,
            "organization_id": user.selected_organization_id,
        },
    )

    # Generate embed script
    embed_script = generate_embed_script(token)

    return EmbedTokenResponse(
        id=token.id,
        token=token.token,
        allowed_domains=token.allowed_domains,
        settings=token.settings,
        is_active=token.is_active,
        usage_count=token.usage_count,
        usage_limit=token.usage_limit,
        expires_at=token.expires_at,
        created_at=token.created_at,
        embed_script=embed_script,
    )


class ShareLinkResponse(BaseModel):
    url: str
    token: str
    is_active: bool
    expires_at: Optional[datetime]
    #: Minutes of calling the link may start per day; null is uncapped.
    daily_minutes_cap: Optional[int]
    minutes_used_today: int


class ShareLinkSettings(BaseModel):
    """What the owner may change about a link after making it."""

    #: Null lifts the cap. Zero is refused: a link that can never start a
    #: call is a switched-off link, and that is the switch, not a cap.
    daily_minutes_cap: Optional[int] = Field(
        None, ge=1, le=share_links.MAX_DAILY_MINUTES
    )
    #: Days from now until the link stops working; null means it does not.
    expires_in_days: Optional[int] = Field(None, ge=1, le=365)
    lift_cap: bool = False
    never_expires: bool = False


async def _share_link_response(token: EmbedTokenModel) -> ShareLinkResponse:
    async with db_client.async_session() as session:
        usage = await share_links.usage_for(
            session, embed_token_id=token.id, cap_minutes=token.daily_minutes_cap
        )
    return ShareLinkResponse(
        url=f"{str(UI_APP_URL).rstrip('/')}/talk/{token.token}",
        token=token.token,
        is_active=bool(token.is_active),
        expires_at=token.expires_at,
        daily_minutes_cap=token.daily_minutes_cap,
        minutes_used_today=usage.minutes_used_today,
    )


async def _share_token(workflow_id: int, user: UserModel) -> Optional[EmbedTokenModel]:
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Agent not found")
    existing = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=False
    )
    return existing[0] if existing else None


@router.post("/{workflow_id}/share-link")
async def create_share_link(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> ShareLinkResponse:
    """A link where anyone talks to this agent in the browser, no account.

    The demo you text a prospect instead of screen-sharing. Backed by the
    same token as the website widget — one per agent, reactivated if it was
    switched off — so the share link and the widget are one thing to revoke.
    Our own host is always allowed for the token (see public_embed), so this
    needs no domain from the customer.

    A new link gets thirty minutes a day and thirty days; switching an old
    one back on gives it a fresh thirty days and keeps whatever cap it had,
    or the default if it never had one. Every call on the link is paid from
    the owner's credits, and the link goes to strangers — the cap is what
    keeps one forwarded message from being a balance gone overnight.
    """
    token = await _share_token(workflow_id, user)
    fresh_expiry = datetime.now(UTC) + timedelta(days=share_links.DEFAULT_EXPIRY_DAYS)
    if token is not None:
        if not token.is_active or (
            token.expires_at and token.expires_at < datetime.now(UTC)
        ):
            token = await db_client.update_embed_token(
                token.id,
                user.selected_organization_id,
                is_active=True,
                expires_at=fresh_expiry,
                daily_minutes_cap=token.daily_minutes_cap
                or share_links.DEFAULT_DAILY_MINUTES,
            )
    else:
        token = await db_client.create_embed_token(
            workflow_id=workflow_id,
            organization_id=user.selected_organization_id,
            created_by=user.id,
            allowed_domains=[],
            settings=sanitize_client_settings(None, None),
            usage_limit=None,
            expires_at=fresh_expiry,
            daily_minutes_cap=share_links.DEFAULT_DAILY_MINUTES,
        )

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.AGENT_SHARED,
        properties={
            "workflow_id": workflow_id,
            "organization_id": user.selected_organization_id,
        },
    )
    return await _share_link_response(token)


@router.get("/{workflow_id}/share-link")
async def get_share_link(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> Optional[ShareLinkResponse]:
    """The agent's link as it stands, or null if none was ever made."""
    token = await _share_token(workflow_id, user)
    return await _share_link_response(token) if token else None


@router.put("/{workflow_id}/share-link")
async def update_share_link(
    workflow_id: int,
    settings: ShareLinkSettings,
    user: UserModel = Depends(get_user),
) -> ShareLinkResponse:
    """Change the link's daily minutes or expiry. Takes effect at once."""
    token = await _share_token(workflow_id, user)
    if token is None:
        raise HTTPException(status_code=404, detail="This agent has no share link yet.")
    changes: dict = {}
    if settings.lift_cap:
        changes["daily_minutes_cap"] = None
    elif settings.daily_minutes_cap is not None:
        changes["daily_minutes_cap"] = settings.daily_minutes_cap
    if settings.never_expires:
        changes["expires_at"] = None
    elif settings.expires_in_days is not None:
        changes["expires_at"] = datetime.now(UTC) + timedelta(
            days=settings.expires_in_days
        )
    if changes:
        token = await db_client.update_embed_token(
            token.id, user.selected_organization_id, **changes
        )
    return await _share_link_response(token)


@router.delete("/{workflow_id}/share-link")
async def switch_off_share_link(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> ShareLinkResponse:
    """The kill switch. The link stops at once; a call in progress ends when
    its session does. Making the link again switches it back on."""
    token = await _share_token(workflow_id, user)
    if token is None:
        raise HTTPException(status_code=404, detail="This agent has no share link yet.")
    token = await db_client.update_embed_token(
        token.id, user.selected_organization_id, is_active=False
    )
    return await _share_link_response(token)


@router.get("/{workflow_id}/embed-token")
async def get_embed_token(
    workflow_id: int,
    request: Request,
    user: UserModel = Depends(get_user),
) -> Optional[EmbedTokenResponse]:
    """
    Get the embed token for a workflow if it exists.
    """
    # Verify workflow exists and user has access
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Get active embed tokens for this workflow
    tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=True
    )

    if not tokens:
        return None

    token = tokens[0]  # There should be only one active token per workflow

    # Generate embed script
    embed_script = generate_embed_script(token)

    return EmbedTokenResponse(
        id=token.id,
        token=token.token,
        allowed_domains=token.allowed_domains,
        settings=token.settings,
        is_active=token.is_active,
        usage_count=token.usage_count,
        usage_limit=token.usage_limit,
        expires_at=token.expires_at,
        created_at=token.created_at,
        embed_script=embed_script,
    )


@router.delete("/{workflow_id}/embed-token")
async def deactivate_embed_token(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> dict:
    """
    Deactivate the embed token for a workflow.
    """
    # Verify workflow exists and user has access
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Get active embed tokens for this workflow
    tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=True
    )

    if not tokens:
        raise HTTPException(
            status_code=404, detail="No active embed token found for this workflow"
        )

    # Deactivate the token
    success = await db_client.deactivate_embed_token(
        tokens[0].id, user.selected_organization_id
    )

    if success:
        return {"message": "Embed token deactivated successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to deactivate embed token")


async def _active_token_or_404(workflow_id: int, user: UserModel):
    """The workflow's active embed token, having checked the caller owns it.

    Both logo routes need the same three steps in the same order, and the
    ordering is the access control: the workflow lookup is scoped to the
    caller's organization, so a workflow id belonging to somebody else is a 404
    before any token is read.
    """
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=True
    )
    if not tokens:
        raise HTTPException(
            status_code=404,
            detail=(
                "This agent has no widget yet. Save the widget settings first, "
                "then add a logo."
            ),
        )
    return tokens[0]


@router.post("/{workflow_id}/embed-token/logo")
async def upload_embed_logo(
    workflow_id: int,
    file: UploadFile = File(...),
    user: UserModel = Depends(get_user),
) -> EmbedTokenResponse:
    """Store a logo for this workflow's widget and point the token at it.

    Read into memory rather than streamed: the cap is 512 KB, and the format
    has to be sniffed from the leading bytes before anything is written
    anywhere. Streaming a file to storage and validating afterwards means a
    rejected upload has already been stored.
    """
    token = await _active_token_or_404(workflow_id, user)

    # Cap the read itself. Trusting UploadFile.size would let a lying client
    # stream an arbitrary amount into this process's memory.
    data = await file.read(MAX_LOGO_BYTES + 1)
    try:
        content_type, extension = validate_logo(data)
    except LogoRejected as rejection:
        raise HTTPException(status_code=400, detail=str(rejection)) from rejection

    backend = get_current_storage_backend()
    key = logo_storage_key(user.selected_organization_id, workflow_id, extension)

    stored = await get_storage().acreate_file_from_bytes(key, data)
    if not stored:
        raise HTTPException(
            status_code=502, detail="Could not store the logo. Try again."
        )

    previous = logo_from_settings(token.settings)

    updated = await db_client.update_embed_token(
        token.id,
        user.selected_organization_id,
        settings=merge_logo_into_settings(
            token.settings,
            key=key,
            content_type=content_type,
            backend=backend.value,
        ),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Embed token not found")

    # Only after the new one is recorded. Deleting first would leave the widget
    # pointing at a missing object if the update failed.
    if (
        previous
        and previous.get("key") != key
        and is_own_logo_key(
            previous.get("key", ""), user.selected_organization_id, workflow_id
        )
    ):
        try:
            await get_storage().adelete_file(previous["key"])
        except Exception as error:  # noqa: BLE001 - a leftover object is not a failure
            logger.warning(f"Could not remove replaced embed logo: {error}")

    return EmbedTokenResponse(
        id=updated.id,
        token=updated.token,
        allowed_domains=updated.allowed_domains,
        settings=updated.settings,
        is_active=updated.is_active,
        usage_count=updated.usage_count,
        usage_limit=updated.usage_limit,
        expires_at=updated.expires_at,
        created_at=updated.created_at,
        embed_script=generate_embed_script(updated),
    )


@router.delete("/{workflow_id}/embed-token/logo")
async def delete_embed_logo(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> EmbedTokenResponse:
    """Drop the logo, and the object behind it."""
    token = await _active_token_or_404(workflow_id, user)
    existing = logo_from_settings(token.settings)

    updated = await db_client.update_embed_token(
        token.id,
        user.selected_organization_id,
        settings=merge_logo_into_settings(
            token.settings, key=None, content_type=None, backend=None
        ),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Embed token not found")

    # Only a key of ours, for this tenant. A row predating the guard could name
    # anything, and a delete is not reversible.
    if existing and is_own_logo_key(
        existing.get("key", ""), user.selected_organization_id, workflow_id
    ):
        try:
            await get_storage().adelete_file(existing["key"])
        except Exception as error:  # noqa: BLE001 - the record is already gone
            logger.warning(f"Could not remove embed logo object: {error}")

    return EmbedTokenResponse(
        id=updated.id,
        token=updated.token,
        allowed_domains=updated.allowed_domains,
        settings=updated.settings,
        is_active=updated.is_active,
        usage_count=updated.usage_count,
        usage_limit=updated.usage_limit,
        expires_at=updated.expires_at,
        created_at=updated.created_at,
        embed_script=generate_embed_script(updated),
    )
