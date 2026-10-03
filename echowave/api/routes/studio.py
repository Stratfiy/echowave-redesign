"""Studio: build agents and a website for them from one chat.

Thin, like every router here: resolve the caller's organisation, delegate to
``services/studio``, shape the reply. Two routers:

- ``/studio`` -- the signed-in screen: the chat, the sites, their builds and
  downloads. Behind the ``studio`` flag for the caller's organisation, and a
  404 while it is off.
- ``/public/sites/{token}/`` -- the preview. No session: the token is the
  only key, and it unlocks nothing but the built files of one site.

**A Studio message is a builder message.** It is metered by the same
allowance and charged past it by the same rule
(``routes/agent_builder.meter_builder_message``), so there is no new price.

**The preview never runs on the app's origin.** A site is code a model wrote,
and served from the api's own host it could otherwise read whatever that
origin can. So unless it is served from a host of its own
(``SITE_PREVIEW_BASE_URL``), every response carries ``Content-Security-Policy:
sandbox``, which gives the page an opaque origin -- no cookies, no storage,
no same-origin requests -- even when somebody opens the URL directly.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api import constants
from api.db import db_client
from api.db.models import UserModel
from api.routes import agent_builder as builder_routes
from api.services import features
from api.services.agent_builder import settings as builder_settings
from api.services.agent_builder.client import BuilderClientError
from api.services.auth.depends import get_user
from api.services.studio import sites
from api.services.studio.session import run_turn

router = APIRouter(
    prefix="/studio",
    tags=["studio"],
    dependencies=[Depends(features.require("studio", per_organization=True))],
)
public_router = APIRouter(prefix="/public/sites", tags=["public-studio"])

#: Longer than the builder's: a Studio transcript carries more tool turns, but
#: :func:`api.services.studio.session.compact` keeps each one small.
MAX_HISTORY_MESSAGES = 160
MAX_MESSAGE_CHARS = 8000


def _organization(user: UserModel) -> int:
    if user.selected_organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user.selected_organization_id


async def _site_or_404(site_id: int, organization_id: int):
    site = await db_client.get_site_project(site_id, organization_id=organization_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


# --- the chat ----------------------------------------------------------------


@router.get("/config")
async def get_studio_config(
    user: Annotated[UserModel, Depends(get_user)],
) -> dict[str, Any]:
    """Whether the chat can run, and what is left of the month's messages."""
    config = await builder_routes.get_builder_config(user)
    return {**config, "builds_configured": bool(constants.SANDBOX_URL)}


class StudioChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARS)
    #: The transcript from the previous reply, sent back unchanged.
    history: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/chat")
async def studio_chat(
    payload: StudioChatRequest,
    user: Annotated[UserModel, Depends(get_user)],
) -> dict[str, Any]:
    organization_id = _organization(user)
    history = payload.history[-MAX_HISTORY_MESSAGES:]
    async with db_client.async_session() as session:
        try:
            model = await builder_settings.resolve_for_organization(
                session, None, organization_id=organization_id
            )
        except builder_settings.BuilderUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        state, charged_credits = await builder_routes.meter_builder_message(
            session, organization_id
        )
        try:
            result = await run_turn(
                session=session,
                model=model,
                organization_id=organization_id,
                user_id=user.id,
                message=payload.message,
                history=history,
            )
        except BuilderClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "reply": result.reply,
        "history": result.conversation,
        "actions": result.actions,
        "created_workflow_ids": result.created_workflow_ids,
        "site_id": result.site_id,
        "connect_links": result.connect_links,
        "usage": builder_routes._usage(state, charged_credits=charged_credits),
    }


# --- sites -------------------------------------------------------------------


@router.get("/sites")
async def list_sites(user: Annotated[UserModel, Depends(get_user)]) -> dict[str, Any]:
    rows = await db_client.list_site_projects(_organization(user))
    return {"sites": [sites.summary(row, include_files=False) for row in rows]}


class CreateSiteRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=sites.MAX_NAME_CHARS)


@router.post("/sites")
async def create_site(
    payload: CreateSiteRequest, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    try:
        site = await sites.create_site(
            organization_id=_organization(user), user_id=user.id, name=payload.name
        )
    except sites.SiteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return sites.summary(site)


@router.get("/sites/{site_id}")
async def get_site(
    site_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    site = await _site_or_404(site_id, _organization(user))
    return {**sites.summary(site), "build_log": site.build_log}


@router.get("/sites/{site_id}/file")
async def get_site_file(
    site_id: int, path: str, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    site = await _site_or_404(site_id, _organization(user))
    try:
        return {"path": path, "content": sites.read_file(site, path)}
    except sites.SiteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class WriteFilesRequest(BaseModel):
    files: list[dict[str, Any]] = Field(default_factory=list)
    delete: list[str] = Field(default_factory=list)


@router.put("/sites/{site_id}/files")
async def write_site_files(
    site_id: int,
    payload: WriteFilesRequest,
    user: Annotated[UserModel, Depends(get_user)],
) -> dict[str, Any]:
    organization_id = _organization(user)
    site = await _site_or_404(site_id, organization_id)
    try:
        return await sites.write_files(
            site,
            organization_id=organization_id,
            writes=payload.files,
            deletes=payload.delete,
        )
    except sites.SiteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sites/{site_id}/build")
async def build_site(
    site_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    organization_id = _organization(user)
    site = await _site_or_404(site_id, organization_id)
    try:
        site, outcome = await sites.build_site(site, organization_id=organization_id)
    except sites.SiteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**outcome.as_result(site), "build_log": site.build_log}


@router.get("/sites/{site_id}/download")
async def download_site(
    site_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> Response:
    site = await _site_or_404(site_id, _organization(user))
    slug = re.sub(r"[^a-z0-9]+", "-", site.name.lower()).strip("-") or "site"
    return Response(
        content=sites.export_zip(site),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )


@router.delete("/sites/{site_id}")
async def delete_site(
    site_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> dict:
    deleted = await db_client.delete_site_project(
        site_id, organization_id=_organization(user)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Site not found")
    return {"deleted": True}


# --- the preview -------------------------------------------------------------

#: The sandbox a preview page runs in when it shares a host with the api.
PREVIEW_SANDBOX = "sandbox allow-scripts allow-forms allow-popups allow-modals"


def _on_preview_host(request: Request) -> bool:
    if not constants.SITE_PREVIEW_BASE_URL:
        return False
    preview_host = urlparse(constants.SITE_PREVIEW_BASE_URL).netloc.lower()
    return (
        bool(preview_host) and request.headers.get("host", "").lower() == preview_host
    )


def _preview_headers(request: Request, path: str) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        # The page's own module scripts are fetched in CORS mode, and from an
        # opaque origin that is a cross-origin request. Public static files,
        # never credentialed, so any origin may read them.
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store" if path == "index.html" else "public, max-age=300",
    }
    if not _on_preview_host(request):
        headers["Content-Security-Policy"] = PREVIEW_SANDBOX
    return headers


async def _serve_preview(token: str, path: str, request: Request) -> Response:
    site = await db_client.get_site_project_by_preview_token(token)
    if site is None or not features.is_on("studio", site.organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    dist = site.dist or {}
    if not dist:
        raise HTTPException(status_code=404, detail="This site has not been built yet.")
    path = path.strip("/") or "index.html"
    if path not in dist:
        # A client-side route (/about, /pricing) is the app's index page; a
        # missing file with an extension is a real 404.
        last = path.rsplit("/", 1)[-1]
        if "." in last:
            raise HTTPException(status_code=404, detail="Not Found")
        path = "index.html"
    body = base64.b64decode(dist[path])
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if media_type.startswith("text/") or media_type in (
        "application/javascript",
        "application/json",
        "image/svg+xml",
    ):
        media_type += "; charset=utf-8"
    return Response(
        content=body,
        media_type=media_type,
        headers=_preview_headers(request, path),
    )


#: Enquiries one visitor may send to one site in ten minutes. A person sends
#: one, maybe two; anything past this is a script.
FORM_SENDS_PER_WINDOW = 5
FORM_WINDOW_SECS = 600
_FORM_HEADERS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"}


def _visitor(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


@public_router.post("/{token}/form", include_in_schema=False)
async def submit_form(token: str, request: Request) -> Response:
    """A visitor sent the site's contact form: start the agent it is connected to.

    Public and cross-origin by design -- the site lives on the business's own
    domain. The page sends ``text/plain`` so the browser needs no preflight;
    the body is JSON either way. Answers 202 once the run is queued; a bot
    that filled the hidden field gets the same 202 and nothing happens.
    """
    import json

    from api.services.rate_limit import rate_limiter
    from api.services.workflow import bot_triggers
    from api.services.workflow.triggered_calls import (
        TriggerRateLimited,
        count_trigger,
    )
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    def answer(status: int, body: dict) -> Response:
        return Response(
            content=json.dumps(body),
            status_code=status,
            media_type="application/json",
            headers=_FORM_HEADERS,
        )

    site = await db_client.get_site_project_by_preview_token(token)
    if site is None or not features.is_on("studio", site.organization_id):
        return answer(404, {"detail": "Not Found"})
    if not site.form_trigger_id:
        return answer(409, {"detail": "This form is not connected yet."})

    raw = await request.body()
    if len(raw) > 32_000:
        return answer(413, {"detail": "That is too long to send."})
    try:
        submitted = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return answer(400, {"detail": "Send the form's fields as JSON."})
    try:
        fields = sites.clean_submission(submitted)
    except sites.FormRejected as exc:
        return answer(exc.status, {"detail": str(exc)})
    if fields is None:
        return answer(202, {"status": "accepted"})

    allowed, retry_after = await rate_limiter.check(
        bucket=f"studio-form:{site.id}",
        identity=_visitor(request),
        limit=FORM_SENDS_PER_WINDOW,
        window_secs=FORM_WINDOW_SECS,
    )
    if not allowed:
        return answer(
            429, {"detail": f"Too many messages; try again in {retry_after} seconds."}
        )

    trigger = await db_client.get_form_trigger_for_site(site)
    if trigger is None or not trigger.is_active:
        return answer(409, {"detail": "This form is not connected yet."})
    try:
        await count_trigger(f"bt:{trigger.uuid}", bot_triggers.MAX_TRIGGERS_PER_HOUR)
    except TriggerRateLimited:
        return answer(
            429, {"detail": "We are getting a lot of messages; try again soon."}
        )

    payload = {"source": "website_form", "site": site.name, **fields}
    try:
        await enqueue_job(FunctionNames.RUN_BOT_TRIGGER, trigger.id, payload, None)
    except Exception:  # noqa: BLE001 - the visitor gets a retry, not a 500
        return answer(503, {"detail": "Could not take your message just now; retry."})
    return answer(202, {"status": "accepted"})


@public_router.get("/{token}/", include_in_schema=False)
async def preview_index(token: str, request: Request) -> Response:
    return await _serve_preview(token, "", request)


@public_router.get("/{token}/{path:path}", include_in_schema=False)
async def preview_file(token: str, path: str, request: Request) -> Response:
    return await _serve_preview(token, path, request)
