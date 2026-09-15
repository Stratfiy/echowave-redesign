"""Inbound email: mail to a bot's address fires the bot (KAN-138).

An email trigger has an address, ``<uuid>@INBOUND_EMAIL_DOMAIN``. The mail
provider (SES/Postmark/SendGrid/Mailgun) is configured to POST each parsed
message here; this route matches it to a trigger by the recipient's local
part, and fires the bot with the mail as the payload — the same run, filter,
dedupe and price as a webhook event.

What this promises the provider:

- **A 200, wherever it can.** A mail provider retries or bounces on a non-2xx,
  and a bounce is a message the sender sees. Unknown recipient, a paused
  trigger, a filtered message: all 200 with a status, nothing runs.
- **A redelivery is not a second run.** The message id dedupes for a day.
- **Only our provider may POST.** When ``INBOUND_EMAIL_TOKEN`` is set it must
  be presented (``X-Inbound-Token`` header or ``?token=``); email addresses
  are guessable-ish and inbound mail is spoofable, so the provider hop is
  authenticated even though the per-message From never is.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from loguru import logger

from api.constants import INBOUND_EMAIL_TOKEN
from api.db import db_client
from api.services.workflow import bot_triggers
from api.services.workflow.triggered_calls import (
    TriggerRateLimited,
    already_delivered,
    count_trigger,
    remember_delivery,
)
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

router = APIRouter(prefix="/public/email", tags=["public-email"])

#: Namespaced so an email event and a webhook event on the same bot never
#: share a dedupe or rate counter.
_KEY_PREFIX = "email:"


async def _read(request: Request) -> dict[str, Any]:
    """The provider's POST, as a flat dict. JSON or form; providers use both."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        try:
            body = await request.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {"data": body}
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001
        return {}
    return {k: v for k, v in form.items()}


@router.post("/inbound", status_code=200)
async def receive_email(
    request: Request,
    x_inbound_token: Annotated[str | None, Header(alias="X-Inbound-Token")] = None,
    token: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    if INBOUND_EMAIL_TOKEN:
        presented = x_inbound_token or token or ""
        if not secrets.compare_digest(presented, INBOUND_EMAIL_TOKEN):
            raise HTTPException(status_code=401, detail="Bad inbound token.")

    raw = await _read(request)
    email = bot_triggers.normalise_email(raw)
    recipient = str(email.get("recipient") or "")
    uuid = bot_triggers.address_uuid(recipient)
    if not uuid:
        return {"status": "no_recipient"}

    trigger = await db_client.get_bot_trigger_by_uuid(uuid)
    if trigger is None or (trigger.source or "") != bot_triggers.SOURCE_EMAIL:
        # Not one of our email triggers. 200 so the provider does not bounce.
        return {"status": "unknown_address"}
    if not trigger.is_active:
        return {"status": "paused", "trigger": trigger.name}

    identifier = f"{_KEY_PREFIX}{trigger.uuid}"
    message_id = str(email.get("message_id") or "") or None
    if await already_delivered(identifier, message_id) is not None:
        return {"status": "duplicate", "trigger": trigger.name}

    if not bot_triggers.matches(trigger.filter, email):
        return {"status": "filtered", "trigger": trigger.name}

    try:
        await count_trigger(identifier, bot_triggers.MAX_TRIGGERS_PER_HOUR)
    except TriggerRateLimited as limited:
        # 429 is the one non-200: a stuck sender flooding the address should be
        # told to stop, and a mail loop is exactly what the cap exists for.
        raise HTTPException(status_code=429, detail=str(limited)) from limited

    await remember_delivery(identifier, message_id, 0)
    try:
        await enqueue_job(FunctionNames.RUN_BOT_TRIGGER, trigger.id, email, message_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not queue email for trigger {}", trigger.id)
        raise HTTPException(
            status_code=503, detail="Could not take the message just now."
        ) from exc
    return {"status": "accepted", "trigger": trigger.name}
