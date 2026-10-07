"""What launch stream `controls` does around a voice session.

Two things, both after the fact and both best-effort, run beside the
existing ``call_*`` PostHog capture in services/pipecat/event_handlers.py:

* the catalogue's ``voice_session_started`` / ``_ended`` / ``_failed``
  (handoff 36: end records duration and disconnect reason), and
* settling a browser voice session against the person's daily voice
  minutes. The first minute was taken when the session started
  (routes/webrtc_signaling.py); the rest is taken from its length here,
  recorded even past the limit -- a live conversation is not cut off
  mid-sentence; the next one is refused.

Neither may ever affect the call: both run after it, and neither raises.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import PostHogEvent, WorkflowRunMode
from api.services import events, quotas

#: The runs that are live voice in a browser, which is what the voice
#: allowance counts. A phone call is metered by billing and telephony.
WEB_VOICE_MODES = frozenset(
    {WorkflowRunMode.WEBRTC.value, WorkflowRunMode.SMALLWEBRTC.value}
)

_EVENT = {
    PostHogEvent.CALL_STARTED.value: "voice_session_started",
    PostHogEvent.CALL_COMPLETED.value: "voice_session_ended",
    PostHogEvent.CALL_FAILED.value: "voice_session_failed",
}


def _reason(code: Any) -> str | None:
    text = str(code or "").strip()
    if not text:
        return None
    return "".join(c if c.isalnum() or c in "_.:-" else "_" for c in text)[:64]


async def after_call_event(
    workflow_run: Any,
    user_provider_id: str | None,
    event: str,
    extra_properties: dict | None = None,
) -> None:
    """Never raises."""
    try:
        name = _EVENT.get(str(getattr(event, "value", event)))
        if workflow_run is None or name is None:
            return
        user = (
            await db_client.get_user_by_provider_id(user_provider_id)
            if user_provider_id
            else None
        )
        workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
        organization_id = getattr(workflow, "organization_id", None)
        started = workflow_run.created_at
        ended = workflow_run.ended_at or datetime.now(UTC)
        seconds = max(0.0, (ended - started).total_seconds()) if started else 0.0
        properties: dict[str, Any] = {"channel": _reason(workflow_run.mode)}
        if name != "voice_session_started":
            properties["duration_ms"] = int(seconds * 1000)
            gathered = workflow_run.gathered_context
            disposition = (
                gathered.get("call_disposition") if isinstance(gathered, dict) else None
            )
            properties["reason_code"] = _reason(
                (extra_properties or {}).get("error_reason") or disposition
            )
        await events.emit(
            name,
            user_id=getattr(user, "id", None),
            organization_id=organization_id,
            task_id=f"run:{workflow_run.id}",
            properties=properties,
        )
        if (
            # Settled once, on the end every session reaches; a failure
            # event may precede it for the same run.
            name == "voice_session_ended"
            and user is not None
            and workflow_run.mode in WEB_VOICE_MODES
        ):
            minutes = max(1, math.ceil(seconds / 60))
            if minutes > 1:
                await quotas.consume(
                    user.id,
                    quotas.VOICE_MINUTES,
                    min(minutes - 1, quotas.MAX_AMOUNT),
                    force=True,
                )
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        logger.warning("Voice controls after {} failed: {}", event, exc)
