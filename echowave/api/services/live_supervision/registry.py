"""The API side: which calls are live, and reaching one of them.

Every read is scoped twice. Redis holds the organisation's set of live run
ids, but an entry there is only a claim; each run is then read from the
database with the viewer's organisation, and a run that does not come back
is not shown. A run id from another workspace is "not found", whichever
worker it is on.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.live_supervision import access, channels
from api.services.workflow import audit_log, visibility

#: The most calls one list shows. A workspace with more than this live at
#: once is told so (``more``) rather than shown a short list as if complete.
LIST_LIMIT = 100


class NotLive(Exception):
    """The run exists in this workspace but is not live (any more)."""


class NotFound(Exception):
    """No such run in this workspace."""


class Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class LiveCall:
    run_id: int
    workflow_id: int
    agent_name: str
    direction: str
    started_at: str
    step: str | None
    caller: str | None
    caller_masked: bool
    can_listen: bool
    blocked: str | None
    agent_owner_id: int | None
    definition: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        started = _parse(self.started_at)
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "agent_name": self.agent_name,
            "direction": self.direction,
            "started_at": self.started_at,
            "duration_seconds": max(0, int(time.time() - started.timestamp()))
            if started
            else 0,
            "step": self.step,
            "caller": self.caller,
            "caller_masked": self.caller_masked,
            "can_listen": self.can_listen,
            "blocked": self.blocked,
        }


def _parse(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def counterparty(run: Any, direction: str) -> str | None:
    """The number on the other end: the caller of an inbound call, the
    person dialled on an outbound one. A web call has none."""
    if direction == "web":
        return None
    context = getattr(run, "initial_context", None) or {}
    if direction == "inbound":
        return context.get("caller_number") or None
    return context.get("called_number") or context.get("phone_number") or None


async def _metas(organization_id: int) -> list[dict[str, Any]]:
    r = channels.redis()
    key = channels.org_key(organization_id)
    now = time.time()
    # Entries of calls whose worker stopped beating long ago are cleared
    # here, by the first reader to notice.
    await r.zremrangebyscore(key, "-inf", now - channels.STALE_SECONDS * 6)
    ids = await r.zrangebyscore(key, now - channels.STALE_SECONDS, "+inf")
    if not ids:
        return []
    raws = await r.mget([channels.meta_key(int(i)) for i in ids])
    metas: list[dict[str, Any]] = []
    for raw in raws:
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except ValueError:
            logger.warning("Unreadable live-call entry in org {}", organization_id)
            continue
        if meta.get("organization_id") != organization_id:
            logger.warning(
                "Live-call entry for run {} names another organisation",
                meta.get("run_id"),
            )
            continue
        metas.append(meta)
    return metas


async def _resolve(
    viewer: access.Viewer, meta: dict[str, Any], *, allowed: bool, workflows: dict
) -> LiveCall | None:
    run_id = int(meta["run_id"])
    run = await db_client.get_workflow_run(
        run_id, organization_id=viewer.organization_id
    )
    if run is None:
        return None
    if run.is_completed:
        # Finished while its entry was still fresh: the next beat would
        # have removed it anyway.
        return None
    workflow = workflows.get(run.workflow_id)
    if workflow is None:
        workflow = await db_client.get_workflow(
            run.workflow_id, organization_id=viewer.organization_id
        )
        workflows[run.workflow_id] = workflow
    if workflow is None or not visibility.visible(workflow, viewer.role):
        return None
    owner = workflow.user_id
    direction = str(meta.get("direction") or "outbound")
    number = counterparty(run, direction)
    may_see = access.may_supervise(viewer, owner)
    blocked = access.blocked_reason(viewer, owner, allowed=allowed)
    definition = getattr(getattr(run, "definition", None), "workflow_json", None)
    return LiveCall(
        run_id=run_id,
        workflow_id=int(run.workflow_id),
        agent_name=str(workflow.name or meta.get("agent_name") or "Agent"),
        direction=direction,
        started_at=str(meta.get("started_at") or ""),
        step=meta.get("step"),
        caller=number if may_see else access.mask_number(number),
        caller_masked=bool(number) and not may_see,
        can_listen=blocked is None,
        blocked=blocked,
        agent_owner_id=owner,
        definition=definition,
    )


async def live_calls(
    viewer: access.Viewer, *, workflow_id: int | None = None
) -> tuple[list[LiveCall], bool, bool]:
    """(calls, allow_listening, more), oldest call first."""
    allowed = await access.allow_listening(viewer.organization_id)
    metas = await _metas(viewer.organization_id)
    if workflow_id is not None:
        metas = [m for m in metas if m.get("workflow_id") == workflow_id]
    metas.sort(key=lambda m: str(m.get("started_at") or ""))
    more = len(metas) > LIST_LIMIT
    workflows: dict = {}
    calls = []
    for meta in metas[:LIST_LIMIT]:
        call = await _resolve(viewer, meta, allowed=allowed, workflows=workflows)
        if call is not None:
            calls.append(call)
    return calls, allowed, more


async def live_call(viewer: access.Viewer, run_id: int) -> LiveCall:
    """One live call, for the panel. Raises ``NotFound`` for a run outside
    the workspace (or an agent the viewer cannot see) and ``NotLive`` for
    one that has ended."""
    run = await db_client.get_workflow_run(
        run_id, organization_id=viewer.organization_id
    )
    if run is None:
        raise NotFound()
    raw = await channels.redis().get(channels.meta_key(run_id))
    meta = json.loads(raw) if raw else None
    if not meta or meta.get("organization_id") != viewer.organization_id:
        raise NotLive()
    allowed = await access.allow_listening(viewer.organization_id)
    call = await _resolve(viewer, meta, allowed=allowed, workflows={})
    if call is None:
        # The run is ours but its agent is hidden from this viewer, or it
        # finished a moment ago.
        if run.is_completed:
            raise NotLive()
        raise NotFound()
    return call


async def backlog(run_id: int) -> list[dict[str, Any]]:
    raws = await channels.redis().lrange(channels.backlog_key(run_id), 0, -1)
    out = []
    for raw in raws:
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


async def require_listener(viewer: access.Viewer, run_id: int) -> LiveCall:
    call = await live_call(viewer, run_id)
    if call.blocked:
        raise Refused(call.blocked)
    return call


async def note_listening(viewer: access.Viewer, call: LiveCall, *, audio: bool) -> None:
    await audit_log.record(
        viewer.organization_id,
        action="call_listened",
        subject_kind="call",
        subject_id=call.run_id,
        subject=f"{call.agent_name} call",
        actor_user_id=viewer.user_id,
        actor=viewer.name,
        after={"audio": bool(audio)},
    )


async def send_whisper(
    viewer: access.Viewer, run_id: int, *, text: str, urgent: bool
) -> dict[str, Any]:
    """Hand an instruction to the worker running the call, and log it.

    Raises ``NotFound``, ``NotLive`` or ``Refused``. A publish that nobody
    receives means no worker is running the call any more: that is
    ``NotLive``, and nothing is logged as whispered.
    """
    from api.services.live_supervision import whisper

    call = await require_listener(viewer, run_id)
    text = whisper.clean_text(text)
    if not text:
        raise Refused("empty")
    whisper_id = uuid.uuid4().hex
    at = datetime.now(UTC).isoformat(timespec="seconds")
    payload = {
        "id": whisper_id,
        "text": text,
        "urgent": bool(urgent),
        "by": viewer.name,
        "by_user_id": viewer.user_id,
        "at": at,
    }
    receivers = await channels.redis().publish(
        channels.control_channel(run_id), json.dumps(payload)
    )
    if not receivers:
        raise NotLive()
    await audit_log.record(
        viewer.organization_id,
        action="call_whispered",
        subject_kind="call",
        subject_id=run_id,
        subject=f"{call.agent_name} call",
        actor_user_id=viewer.user_id,
        actor=viewer.name,
        after={"text": text, "urgent": bool(urgent), "tag": whisper.tag(viewer.name)},
    )
    return {"id": whisper_id, "at": at, "urgent": bool(urgent), "text": text}
