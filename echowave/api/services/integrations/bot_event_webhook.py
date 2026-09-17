"""A bot posting its own events to a URL somebody pasted in.

The inbound half has existed for a while: every bot has a trigger URL and an
address, so n8n can start a bot from a Shopify order. Nothing went the other
way. A bot that filed an outcome could tell the thread and the bell and
nothing else, which makes this product the end of a chain rather than a link
in one -- and "does it integrate with what we already run" is the question
that decides the sale.

So: one URL per bot, and its events are POSTed there.

**What goes out is the bot's own choice, and the default is the bell's.** A
webhook with no kinds named is not off -- it carries the kinds a person can
subscribe to on the bell, resolved when the event happens rather than copied
into the row. A kind added to the product later reaches a webhook set up
today, which is the opposite of the failure the allowlist in
``agent_timeline`` carries a warning about. Naming kinds explicitly is
allowed and is not limited to that set: the API takes any kind the timeline
can write, because somebody wiring a real pipeline knows what they want and
a curated list is the screen's job, not the endpoint's.

**Signed, because a URL is not a secret.** Anybody who learns it can POST to
it. The receiver gets an HMAC of the exact body it is about to parse, plus
the timestamp that HMAC covers, so a replay of yesterday's delivery can be
told from today's.

**Never raises into the thing that happened.** Same posture as the bell
beside it: an event is recorded first and told to the outside after, and a
receiver that is down must not be able to undo a row. A failed POST is the
delivery engine's problem, and it retries.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from loguru import logger

from api.constants import DEFAULT_WEBHOOK_DELIVERY_CONFIG, EVENT_WEBHOOK_HOURLY_CAP

#: Header names. Prefixed like the delivery id the engine already sends, so a
#: receiver reads one family of headers from us.
SIGNATURE_HEADER = "X-Decibyl-Signature"
TIMESTAMP_HEADER = "X-Decibyl-Timestamp"
EVENT_HEADER = "X-Decibyl-Event"

#: Long enough that guessing is not a strategy, short enough to paste.
SECRET_BYTES = 24


def new_secret() -> str:
    """A signing secret, shown to the person once."""
    return secrets.token_hex(SECRET_BYTES)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """The signature a receiver recomputes.

    Over the timestamp and the body together: signing the body alone lets
    somebody replay a delivery for ever, and a timestamp nobody signed is a
    timestamp anybody can change.
    """
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def wanted(kinds: Any, kind: str) -> bool:
    """Whether this webhook asked for this kind.

    Empty means the bell's set, read now rather than at save time, so a kind
    the product gains later reaches a webhook nobody has touched since.
    """
    from api.services.workflow import bot_notices

    named = [k.strip() for k in (kinds or []) if isinstance(k, str) and k.strip()]
    if not named:
        return kind in bot_notices.NOTIFIABLE
    return kind in named


def payload_for(
    *,
    kind: str,
    summary: str,
    workflow_id: Optional[int],
    workflow_name: Optional[str],
    organization_id: int,
    event_id: Optional[int],
    payload: Optional[dict[str, Any]],
    at: Optional[datetime] = None,
) -> dict[str, Any]:
    """The body the receiver gets.

    The sentence as well as the payload. The summary is written once, when it
    happens, and is the line a person reads on the timeline -- so a receiver
    that only wants to put it in Slack does not have to reassemble it from
    fields, and one that wants the detail still has it.
    """
    return {
        "event": kind,
        "summary": summary,
        "at": (at or datetime.now(UTC)).isoformat(),
        "bot": {"id": workflow_id, "name": workflow_name},
        "organization_id": organization_id,
        "event_id": event_id,
        "data": payload or {},
    }


def body_of(document: dict[str, Any]) -> bytes:
    """The exact bytes that are signed and sent.

    Serialised once, here, because a signature over a different encoding of
    the same dict is a signature that fails at the receiver for reasons
    nobody can see. ``sort_keys`` so a re-send is byte-identical.
    """
    return json.dumps(document, sort_keys=True, default=str).encode()


async def post_event(
    *,
    organization_id: int,
    workflow_id: Optional[int],
    kind: str,
    summary: str,
    event_id: Optional[int],
    payload: Optional[dict[str, Any]],
) -> None:
    """Queue this event for the bot's webhook, if it has one that wants it.

    Silent on every failure. The row is already written and the bell has
    already rung; a receiver's URL being unreachable, or this function
    having a bug, must not reach the caller.
    """
    if workflow_id is None:
        # Decibyl's own rows and channel rows have no bot, and the field this
        # reads is per bot. An account-wide destination is a different
        # feature and would need its own field to be honest about.
        return
    try:
        from api.db import db_client

        hook = await db_client.get_bot_event_webhook(
            workflow_id, organization_id=organization_id
        )
        if hook is None or not hook.is_active or not wanted(hook.kinds, kind):
            return

        # A receiver is somebody else's server. A bot that files an outcome
        # per row of a sheet would otherwise POST to it as fast as it can
        # think, and the retries on a receiver that buckles make it worse.
        # Over the cap the event is still on the timeline and still rang
        # the bell; only the POST is withheld, and the log says so.
        since = datetime.now(UTC) - timedelta(hours=1)
        sent = await db_client.count_bot_event_deliveries_since(
            organization_id=organization_id, since=since
        )
        if sent >= EVENT_WEBHOOK_HOURLY_CAP:
            logger.warning(
                "Event webhook for org {} withheld: {} sent in the last hour "
                "(cap {}); {} on bot {} not posted",
                organization_id,
                sent,
                EVENT_WEBHOOK_HOURLY_CAP,
                kind,
                workflow_id,
            )
            return

        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        document = payload_for(
            kind=kind,
            summary=summary,
            workflow_id=workflow_id,
            workflow_name=getattr(workflow, "name", None),
            organization_id=organization_id,
            event_id=event_id,
            payload=payload,
        )

        # One delivery per bot per event. Without an event id there is
        # nothing stable to dedupe on, so the moment stands in for it --
        # a duplicate is then possible only if the same event is recorded
        # twice in the same microsecond, which is a duplicate row anyway.
        node_key = (
            f"event:{event_id}"
            if event_id is not None
            else f"event:{kind}:{datetime.now(UTC).timestamp()}"
        )

        delivery, created = await db_client.create_bot_event_delivery(
            workflow_id=workflow_id,
            organization_id=organization_id,
            endpoint_url=hook.url,
            payload=document,
            max_attempts=DEFAULT_WEBHOOK_DELIVERY_CONFIG["max_attempts"],
            node_key=node_key,
            webhook_name=f"{getattr(workflow, 'name', 'bot')} events",
        )
        if not created or delivery is None:
            return

        from api.tasks.arq import enqueue_job
        from api.tasks.function_names import FunctionNames

        await enqueue_job(
            FunctionNames.DELIVER_WEBHOOK,
            delivery.id,
            _job_id=f"webhook-delivery-{delivery.id}-0",
        )
    except Exception as exc:  # noqa: BLE001 - a webhook must not undo a row
        logger.warning("Could not queue the event webhook for {}: {}", kind, exc)


async def send_test(
    *,
    organization_id: int,
    workflow_id: int,
    workflow_name: Optional[str],
    url: str,
) -> bool:
    """One sample delivery, through the real engine.

    The first thing anybody wiring a webhook needs is a POST to look at, so
    the flow on the other end can be built against the real body and the real
    headers. Queued the same way a real event is -- same signing, same
    retries, same delivery id -- because a test that takes a different path
    proves nothing about the path events take.

    Its own event name rather than a fake outcome: a receiver must be able to
    tell a test from the real thing, or somebody's Slack channel gets a
    refund notice that never happened.
    """
    try:
        from api.db import db_client

        document = payload_for(
            kind="decibyl.test",
            summary="A test delivery from Decibyl. Nothing happened.",
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            organization_id=organization_id,
            event_id=None,
            payload={"test": True},
        )
        delivery, _ = await db_client.create_bot_event_delivery(
            workflow_id=workflow_id,
            organization_id=organization_id,
            endpoint_url=url,
            payload=document,
            max_attempts=1,
            # Unique per press, so pressing Test twice sends twice. A test
            # deduped against an earlier one looks like a webhook that
            # stopped working.
            node_key=f"test:{datetime.now(UTC).timestamp()}",
            webhook_name=f"{workflow_name or 'bot'} events (test)",
        )
        if delivery is None:
            return False

        from api.tasks.arq import enqueue_job
        from api.tasks.function_names import FunctionNames

        await enqueue_job(
            FunctionNames.DELIVER_WEBHOOK,
            delivery.id,
            _job_id=f"webhook-delivery-{delivery.id}-0",
        )
        return True
    except Exception as exc:  # noqa: BLE001 - the answer is the boolean
        logger.warning(
            "Could not queue a webhook test for bot {}: {}", workflow_id, exc
        )
        return False
