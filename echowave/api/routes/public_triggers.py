"""The doorbell itself: where an outside system posts an event (KAN-137).

No API key. The senders that matter -- Shopify, Razorpay, Zoho, a Google
Form through Zapier -- can set a URL and, at best, one header; they cannot
sign requests our way. So the address is an unguessable uuid and the
trigger has its own secret, presented in ``X-Trigger-Secret`` or as
``?key=`` for senders that cannot set headers. The tenant is derived from
the trigger row, which the secret proves the caller may ring.

What this route promises a sender:

- **202 and done.** The run happens on a worker. A sender waiting on a
  model turn would time out and retry, and a retry is a second run.
- **A redelivery is not a second run.** ``X-Event-Id``, or ``event_id`` /
  ``id`` in the body, is remembered for a day.
- **An event the operator did not ask about costs nothing.** The filter is
  checked here, before anything is queued.
- **A paused trigger still answers 202.** A 4xx is what makes a sender
  give up on the address for good.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from loguru import logger

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

router = APIRouter(prefix="/public/triggers", tags=["public-triggers"])

#: Keys in the trigger safety store are namespaced so a bot trigger and a
#: call trigger on the same bot never share a counter.
_KEY_PREFIX = "bt:"


async def _read_payload(request: Request) -> dict[str, Any]:
    """Whatever was sent, as a dict. A body that is not JSON is kept as text
    rather than refused: the bot can read a form-encoded order too."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        body = await request.json()
    except ValueError:
        text = raw.decode("utf-8", errors="replace")
        form = None
        if request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            try:
                form = dict((await request.form()).items())
            except Exception:  # noqa: BLE001
                form = None
        return form or {"raw": text[: bot_triggers.MAX_PAYLOAD_CHARS]}
    if isinstance(body, dict):
        return body
    return {"data": body}


def _event_id(header: str | None, payload: dict[str, Any]) -> str | None:
    for candidate in (header, payload.get("event_id"), payload.get("id")):
        if candidate not in (None, ""):
            return str(candidate)[:120]
    return None


@router.post("/{trigger_uuid}", status_code=202)
async def receive_event(
    trigger_uuid: str,
    request: Request,
    x_trigger_secret: Annotated[str | None, Header(alias="X-Trigger-Secret")] = None,
    x_event_id: Annotated[str | None, Header(alias="X-Event-Id")] = None,
    key: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Ring the bot. Returns ``accepted``, ``duplicate``, ``filtered`` or
    ``paused`` -- every one of them a 202, for the reasons in the module
    docstring."""
    trigger = await db_client.get_bot_trigger_by_uuid(trigger_uuid)
    if trigger is None:
        raise HTTPException(status_code=404, detail="No such trigger.")
    presented = x_trigger_secret or key or ""
    if not presented or not secrets.compare_digest(presented, trigger.secret or ""):
        raise HTTPException(status_code=401, detail="Wrong or missing trigger secret.")

    if not trigger.is_active:
        return {"status": "paused", "trigger": trigger.name}

    payload = await _read_payload(request)
    event_id = _event_id(x_event_id, payload)
    identifier = f"{_KEY_PREFIX}{trigger.uuid}"

    duplicate = await already_delivered(identifier, event_id)
    if duplicate is not None:
        logger.info("Event {} already delivered to trigger {}", event_id, trigger.id)
        return {"status": "duplicate", "trigger": trigger.name}

    if not bot_triggers.matches(trigger.filter, payload):
        return {
            "status": "filtered",
            "trigger": trigger.name,
            "filter": bot_triggers.describe(trigger.filter),
        }

    try:
        await count_trigger(identifier, bot_triggers.MAX_TRIGGERS_PER_HOUR)
    except TriggerRateLimited as limited:
        raise HTTPException(status_code=429, detail=str(limited)) from limited

    # Remembered before the job exists, so two retries arriving together
    # cannot both queue. The value is a placeholder; nothing reads it back
    # except the "is this a duplicate" check.
    await remember_delivery(identifier, event_id, 0)
    try:
        await enqueue_job(FunctionNames.RUN_BOT_TRIGGER, trigger.id, payload, event_id)
    except Exception as exc:
        logger.exception("Could not queue event for trigger {}", trigger.id)
        raise HTTPException(
            status_code=503, detail="Could not take the event just now; retry."
        ) from exc
    return {"status": "accepted", "trigger": trigger.name}
