"""Server-owned product events, through a durable outbox (handoff 35, 36).

Server events establish outcomes -- a task completed, an approval executed,
a delivery made, a cost recorded. Client events only describe intent. So the
events that matter are written here, by the code that changed the state, in
the **same transaction** as the change: ``record`` adds an outbox row to the
caller's session and returns. If the business write rolls back, so does the
event; if PostHog is down, the business write does not notice.

``dispatch_pending`` (an ARQ cron) sends what is waiting. Each event carries
its ``event_id`` as PostHog's ``uuid``, so a delivery retried after a
timeout is still one event there. Analytics never blocks a voice response:
nothing here is awaited on the call path except one INSERT.

**The envelope** is the handoff's: event_id, schema_version, occurred_at in
UTC, environment, release, pseudonymous user_id, workspace_id and task_id
where applicable, trace_id and configuration_version. Properties pass
through ``redaction.redact_properties``: scalars only, no content-named
fields, strings capped and scrubbed.

**Pseudonymous** means an HMAC of the user id under
``ANALYTICS_PSEUDONYM_KEY``. Without that key nothing leaves the outbox: a
plain hash of a small integer is a lookup table away from the integer.
Hashing does not make the data anonymous, and this module does not claim it.

**Names** are the handoff catalogue's. The ``controls`` stream owns the
repository's event catalogue; until it merges, ``SERVER_EVENTS`` below is the
list this module accepts, and an unknown name is refused loudly rather than
sent under a name nobody's dashboard reads.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.ops_models import AnalyticsOutboxModel
from api.services import features
from api.services.ops import redaction

SCHEMA_VERSION = 1
FLAG = "server_analytics"

#: Handoff 36 / design "Metrics events and release proof". Server-owned only:
#: client intent events (clicks, views) do not belong in this outbox.
SERVER_EVENTS = frozenset(
    {
        "invite_accepted",
        "onboarding_completed",
        "first_useful_task_completed",
        "task_started",
        "task_completed",
        "task_failed",
        "task_cancelled",
        "approval_requested",
        "approval_granted",
        "approval_rejected",
        "approval_expired",
        "voice_session_started",
        "voice_session_ended",
        "voice_session_failed",
        "connection_started",
        "connection_ready",
        "connection_revoked",
        "support_action_requested",
        "support_action_approved",
        "support_action_executed",
        "support_action_failed",
        "evaluation_completed",
        "usage_cost_recorded",
        # Stream ops: the operations themselves, so a funnel can show when an
        # incident or a cost stop overlapped a drop.
        "ops_command_executed",
        "cost_stop_engaged",
        "cost_stop_released",
    }
)

#: Typed properties every event may carry beside the envelope. Anything else
#: still goes through redaction, but these are the ones dashboards rely on.
TYPED_PROPERTIES = (
    "channel",
    "language",
    "agent_type",
    "status",
    "reason_code",
    "duration_ms",
)

MAX_BATCH = 200
MAX_ATTEMPTS = 8


class UnknownEvent(ValueError):
    """An event name that is not in the catalogue."""


def pseudonymous_id(user_id: int | str | None) -> str | None:
    """HMAC-SHA256 of the user id, or None when there is no user or no key."""
    if user_id is None or not constants.ANALYTICS_PSEUDONYM_KEY:
        return None
    digest = hmac.new(
        constants.ANALYTICS_PSEUDONYM_KEY.encode(),
        str(user_id).encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"u_{digest[:32]}"


@dataclass(frozen=True)
class Envelope:
    event_id: str
    event: str
    occurred_at: datetime
    user_id: str | None
    workspace_id: int | None
    task_id: str | None
    trace_id: str | None
    configuration_version: str | None
    properties: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        from api.services.system_status import build_info

        return {
            "event_id": self.event_id,
            "schema_version": SCHEMA_VERSION,
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
            "environment": constants.ENVIRONMENT,
            "release": build_info()["git_sha"],
            # Explicit nulls: a field that is not applicable is null, never
            # missing, so a dashboard can tell "none" from "forgot".
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "configuration_version": self.configuration_version,
            "properties": self.properties,
        }


def build(
    event: str,
    *,
    user_id: int | None = None,
    workspace_id: int | None = None,
    task_id: str | int | None = None,
    trace_id: str | None = None,
    configuration_version: str | None = None,
    occurred_at: datetime | None = None,
    event_id: str | None = None,
    properties: dict[str, Any] | None = None,
) -> Envelope:
    """Validate the name and redact the properties. Pure; no I/O."""
    if event not in SERVER_EVENTS:
        raise UnknownEvent(event)
    props = dict(properties or {})
    if (
        props.get("reason_code") is not None
        and props["reason_code"] not in redaction.REASON_CODES
    ):
        props["reason_code"] = redaction.reason_code(str(props["reason_code"]))
    return Envelope(
        event_id=event_id or uuid.uuid4().hex,
        event=event,
        occurred_at=(occurred_at or datetime.now(UTC)),
        user_id=pseudonymous_id(user_id),
        workspace_id=workspace_id,
        task_id=None if task_id is None else str(task_id),
        trace_id=trace_id,
        configuration_version=configuration_version,
        properties=redaction.redact_properties(props),
    )


async def record(session: AsyncSession, event: str, **kwargs: Any) -> str | None:
    """Add the event to the outbox inside the caller's transaction.

    Returns the event id, or None while ``server_analytics`` is off. Raises
    ``UnknownEvent`` for a name outside the catalogue -- a typo should fail a
    test, not vanish.
    """
    envelope = build(event, **kwargs)
    if not features.is_on(FLAG, kwargs.get("workspace_id")):
        return None
    session.add(
        AnalyticsOutboxModel(
            event_id=envelope.event_id,
            event=envelope.event,
            payload=envelope.payload(),
            occurred_at=envelope.occurred_at,
        )
    )
    return envelope.event_id


def _distinct_id(payload: dict[str, Any]) -> str:
    from api.services.posthog_client import POSTHOG_SYSTEM_DISTINCT_ID

    return payload.get("user_id") or POSTHOG_SYSTEM_DISTINCT_ID


async def dispatch_pending(session: AsyncSession, *, client=None) -> dict[str, int]:
    """Send waiting events to PostHog. Returns counts; never raises.

    Rows are claimed with ``SKIP LOCKED`` so two workers never send the same
    batch. A row that keeps failing stops being retried after
    ``MAX_ATTEMPTS`` and stays visible as undelivered.
    """
    from api.services.posthog_client import get_posthog

    counts = {"sent": 0, "failed": 0, "skipped": 0}
    if not features.is_on(FLAG):
        counts["skipped"] = -1
        return counts
    if not constants.ANALYTICS_PSEUDONYM_KEY:
        logger.warning(
            "server_analytics is on but ANALYTICS_PSEUDONYM_KEY is unset; "
            "the outbox is held rather than sending reversible ids"
        )
        counts["skipped"] = -1
        return counts
    client = client or get_posthog()
    if client is None:
        counts["skipped"] = -1
        return counts

    rows = (
        await session.scalars(
            select(AnalyticsOutboxModel)
            .where(
                AnalyticsOutboxModel.delivered_at.is_(None),
                AnalyticsOutboxModel.attempts < MAX_ATTEMPTS,
            )
            .order_by(AnalyticsOutboxModel.created_at)
            .limit(MAX_BATCH)
            .with_for_update(skip_locked=True)
        )
    ).all()
    now = datetime.now(UTC)
    for row in rows:
        payload = row.payload or {}
        groups = (
            {"organization": str(payload["workspace_id"])}
            if payload.get("workspace_id") is not None
            else None
        )
        properties = {
            **(payload.get("properties") or {}),
            **{k: v for k, v in payload.items() if k != "properties"},
        }
        try:
            kwargs: dict[str, Any] = {
                "distinct_id": _distinct_id(payload),
                "event": row.event,
                "properties": properties,
                "timestamp": row.occurred_at,
                "uuid": row.event_id,
            }
            if groups:
                kwargs["groups"] = groups
            client.capture(**kwargs)
            row.delivered_at = now
            counts["sent"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the batch
            row.attempts = (row.attempts or 0) + 1
            row.last_error_code = redaction.reason_code(exc)
            counts["failed"] += 1
    await session.commit()
    return counts


async def outbox_health(session: AsyncSession) -> dict[str, Any]:
    """Backlog and the age of the oldest undelivered event, for the console."""
    pending, oldest = (
        await session.execute(
            select(
                func.count(AnalyticsOutboxModel.id),
                func.min(AnalyticsOutboxModel.created_at),
            ).where(AnalyticsOutboxModel.delivered_at.is_(None))
        )
    ).one()
    stuck = await session.scalar(
        select(func.count(AnalyticsOutboxModel.id)).where(
            AnalyticsOutboxModel.delivered_at.is_(None),
            AnalyticsOutboxModel.attempts >= MAX_ATTEMPTS,
        )
    )
    age = None
    if oldest is not None:
        age = round((datetime.now(UTC) - oldest).total_seconds(), 1)
    return {
        "pending": int(pending or 0),
        "stuck": int(stuck or 0),
        "oldest_age_seconds": age,
    }
