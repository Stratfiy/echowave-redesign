"""Ops events into the controls event catalogue's outbox (handoff 35, 36).

The ``controls`` stream owns the catalogue (``services/events/catalogue.py``),
the envelope and its privacy checks (``envelope.py``), the transactional
outbox and its delivery to PostHog (``outbox.py``, the
``deliver_analytics_outbox`` cron). This module is the ops side's door to
it, and adds two things in front:

* **The ``server_analytics`` switch.** Ops and cost events are written only
  while it is on (per workspace), *and* the catalogue's own
  ``event_catalogue`` switch is on -- the outbox is controls'.
* **Redaction first.** Properties go through ``redaction.redact_properties``
  (content-named and non-scalar values dropped) and a free-text
  ``reason_code`` becomes a code, before the catalogue's strict check. So an
  ops caller passing something it should not loses that property instead of
  losing the whole event to a refusal.

``record`` joins the caller's transaction, never raises for a refused event
(the outbox logs it as an error), and raises ``UnknownEvent`` only for a name
the catalogue does not have -- a typo should fail a test, not vanish.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services import features
from api.services.events import catalogue
from api.services.ops import redaction

FLAG = "server_analytics"


class UnknownEvent(ValueError):
    """An event name that is not in the catalogue."""


def prepare(event: str, properties: dict[str, Any] | None) -> dict[str, Any]:
    """The properties as the catalogue will see them. Pure."""
    if event not in catalogue.CATALOGUE:
        raise UnknownEvent(event)
    props = dict(properties or {})
    code = props.get("reason_code")
    if code is not None and code not in redaction.REASON_CODES:
        props["reason_code"] = redaction.reason_code(str(code))
    cleaned = redaction.redact_properties(props)
    # The catalogue refuses unknown keys; what redaction dropped is not sent.
    cleaned.pop("_redacted", None)
    allowed = catalogue.get(event).allowed
    return {k: v for k, v in cleaned.items() if k in allowed}


def enabled(workspace_id: int | None = None) -> bool:
    from api.services.events import outbox

    return features.is_on(FLAG, workspace_id) and outbox.enabled()


async def record(
    session: AsyncSession,
    event: str,
    *,
    user_id: int | None = None,
    workspace_id: int | None = None,
    task_id: str | int | None = None,
    trace_id: str | None = None,
    configuration_version: str | None = None,
    occurred_at: datetime | None = None,
    properties: dict[str, Any] | None = None,
) -> str | None:
    """Add the event to the outbox inside the caller's transaction. Returns
    the event id, or None while switched off or when the outbox refused it."""
    from api.services.events import outbox

    props = prepare(event, properties)
    if not enabled(workspace_id):
        return None
    return await outbox.emit(
        event,
        session=session,
        user_id=user_id,
        organization_id=workspace_id,
        task_id=task_id,
        trace_id=trace_id,
        configuration_version=configuration_version,
        occurred_at=occurred_at,
        properties=props,
    )


async def outbox_health(session: AsyncSession) -> dict[str, Any]:
    """Backlog of the shared outbox and the age of its oldest undelivered
    event, for the operations console."""
    from api.db.controls_models import AnalyticsOutboxModel
    from api.services.events.outbox import MAX_ATTEMPTS

    pending, oldest = (
        await session.execute(
            select(
                func.count(AnalyticsOutboxModel.event_id),
                func.min(AnalyticsOutboxModel.created_at),
            ).where(AnalyticsOutboxModel.delivered_at.is_(None))
        )
    ).one()
    stuck = await session.scalar(
        select(func.count(AnalyticsOutboxModel.event_id)).where(
            AnalyticsOutboxModel.delivered_at.is_(None),
            AnalyticsOutboxModel.attempts >= MAX_ATTEMPTS,
        )
    )
    age = None
    if oldest is not None:
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        age = round((datetime.now(UTC) - oldest).total_seconds(), 1)
    return {
        "pending": int(pending or 0),
        "stuck": int(stuck or 0),
        "oldest_age_seconds": age,
    }
