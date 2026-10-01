"""Webhooks for Decibyl in your apps (DCH-1, KAN-277): Telegram, Slack, Teams.

Public and never authenticated as a user: each platform proves itself its own
way, checked before the body is trusted. Always a 200 once the proof holds, so
a platform never retries a message we chose to drop.

Slack and Teams want an answer within a few seconds, and a Decibyl turn takes
longer, so their messages are handled after the response is sent.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from loguru import logger

from api.constants import UI_APP_URL
from api.services.messaging import whatsapp_inbound
from api.services.messaging.channels import dispatch, slack, teams, telegram
from api.services.messaging.channels.base import Inbound

router = APIRouter(prefix="/public", tags=["public-decibyl-channels"])


@router.post("/telegram/webhook", status_code=200)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
) -> dict[str, Any]:
    if not telegram.verify(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Bad secret.")
    try:
        update = await request.json()
    except ValueError:
        return {"status": "unreadable"}
    inbound = telegram.parse(update)
    if inbound is None:
        return {"status": "ignored"}
    # Telegram retries until it gets a 200; twice is once.
    if await whatsapp_inbound.seen_before(f"telegram:{inbound.message_id}"):
        return {"status": "duplicate"}
    try:
        status = await dispatch.handle(inbound)
    except Exception as exc:  # noqa: BLE001 - never make Telegram retry
        logger.exception("Telegram update {} failed: {}", inbound.message_id, exc)
        status = "error"
    return {"status": status}


async def _handle_quietly(inbound: Inbound) -> None:
    """Run the dispatcher after the platform has had its 200."""
    try:
        status = await dispatch.handle(inbound)
        logger.info("{} message {}: {}", inbound.channel, inbound.message_id, status)
    except Exception as exc:  # noqa: BLE001 - nothing to retry into
        logger.exception(
            "{} message {} failed: {}", inbound.channel, inbound.message_id, exc
        )


def slack_redirect_uri() -> str:
    """Where Slack sends the browser after "Add to Slack":
    ``{BACKEND_API_ENDPOINT}/api/v1/public/slack/oauth/callback``, on the API
    host. Registered on the Slack app exactly as written here."""
    return slack.redirect_uri()


async def _slack_body(
    request: Request, timestamp: str | None, signature: str | None
) -> bytes:
    raw = await request.body()
    if not slack.verify(raw, timestamp, signature):
        raise HTTPException(status_code=403, detail="Bad signature.")
    return raw


@router.post("/slack/events", status_code=200)
async def slack_events(
    request: Request,
    background: BackgroundTasks,
    x_slack_request_timestamp: Annotated[
        str | None, Header(alias="X-Slack-Request-Timestamp")
    ] = None,
    x_slack_signature: Annotated[str | None, Header(alias="X-Slack-Signature")] = None,
) -> dict[str, Any]:
    raw = await _slack_body(request, x_slack_request_timestamp, x_slack_signature)
    try:
        import json

        payload = json.loads(raw or b"{}")
    except ValueError:
        return {"status": "unreadable"}
    # Slack checks the endpoint once, when the URL is saved in the app config.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    inbound = slack.parse_event(payload)
    if inbound is None:
        return {"status": "ignored"}
    if await whatsapp_inbound.seen_before(f"slack:{inbound.message_id}"):
        return {"status": "duplicate"}
    background.add_task(_handle_quietly, inbound)
    return {"status": "accepted"}


@router.post("/slack/interactions", status_code=200)
async def slack_interactions(
    request: Request,
    background: BackgroundTasks,
    x_slack_request_timestamp: Annotated[
        str | None, Header(alias="X-Slack-Request-Timestamp")
    ] = None,
    x_slack_signature: Annotated[str | None, Header(alias="X-Slack-Signature")] = None,
) -> dict[str, Any]:
    raw = await _slack_body(request, x_slack_request_timestamp, x_slack_signature)
    inbound = slack.parse_interaction(slack.form_payload(raw))
    if inbound is None:
        return {"status": "ignored"}
    if await whatsapp_inbound.seen_before(f"slack:{inbound.message_id}"):
        return {"status": "duplicate"}
    background.add_task(_handle_quietly, inbound)
    return {"status": "accepted"}


@router.get("/slack/oauth/callback", include_in_schema=False)
async def slack_oauth_callback(
    code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    """Finish "Add to Slack" and send the browser back to Settings."""
    from urllib.parse import quote

    settings = f"{UI_APP_URL}/settings"
    if error or not code or not state:
        return RedirectResponse(
            f"{settings}?slack_error={quote('Slack was not added.')}", status_code=303
        )
    try:
        team = await slack.complete_install(
            code=code, state=state, redirect_uri=slack_redirect_uri()
        )
    except ValueError as exc:
        return RedirectResponse(
            f"{settings}?slack_error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(
        f"{settings}?slack_installed={quote(team)}", status_code=303
    )


@router.post("/teams/messages", status_code=200)
async def teams_messages(
    request: Request,
    background: BackgroundTasks,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    try:
        activity = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Unreadable.") from None
    if not isinstance(activity, dict):
        raise HTTPException(status_code=400, detail="Unreadable.")
    # The signing keys are fetched over the network on first use and cached.
    if not await run_in_threadpool(teams.verify, authorization, activity):
        raise HTTPException(status_code=403, detail="Bad token.")
    inbound = teams.parse(activity)
    if inbound is None:
        return {"status": "ignored"}
    if await whatsapp_inbound.seen_before(f"teams:{inbound.message_id}"):
        return {"status": "duplicate"}
    background.add_task(_handle_quietly, inbound)
    return {"status": "accepted"}
