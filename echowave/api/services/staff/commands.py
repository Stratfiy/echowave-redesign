"""The staff console's own typed, approved commands (handoff 32-33, design
"Operational action").

Everything the console changes goes through here: role grants, invitations,
suspensions, refunds, incidents and evaluation work. Infrastructure and
platform operations (pauses, flag changes, drains, key rotation) are the ops
stream's commands at ``/api/v1/admin/ops/commands`` and are not repeated
here; the console calls those directly.

Each command is a ``CommandSpec``: a name, the capability that may ask for
it, the capability that must approve it (a different person) or none, a
pydantic target, an eligibility check, a preview and a handler. A request
carries the full contract -- command, the roles it was asked under,
environment, target, reason, approval, idempotency key, result -- and is
written to ``staff_commands`` before anything changes.

    requested --(no approval)--> queued (worker) | running (inline) --> succeeded | failed
              \\--(approval)--> awaiting_approval --approve--> queued | running ...
                                                 \\--reject--> rejected
                                                 \\--timeout--> expired
    a worker that died mid-run ---------------------------------> outcome_unknown

**Accepted is queued, not succeeded.** A worker command answers ``queued``
and the ARQ job ``run_staff_command`` executes it once, by compare-and-swap
from queued to running. Bookkeeping commands (an incident note, a case
draft) run inline in the request, because there is nothing external to wait
for and the row is the outcome.

**Idempotency.** The same key returns the same request. The same key with a
different command, target or requester is a conflict, never a second action.

**Audit.** Request, approval, rejection and the final outcome each write a
row to ``admin_action_log`` with the command id, so the audit correlates a
change to its approval and result without copying the target into it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db import db_client
from api.db.models import AdminActionLogModel
from api.db.staff_models import StaffCommandModel
from api.services import features
from api.services.staff import roles as staff_roles

AWAITING_APPROVAL = "awaiting_approval"
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
REJECTED = "rejected"
EXPIRED = "expired"
OUTCOME_UNKNOWN = "outcome_unknown"
TERMINAL = (SUCCEEDED, FAILED, REJECTED, EXPIRED, OUTCOME_UNKNOWN)

APPROVAL_WINDOW = timedelta(hours=24)
REQUEUE_AFTER = timedelta(minutes=2)
UNKNOWN_AFTER = timedelta(minutes=15)


class CommandError(ValueError):
    """A request that cannot be accepted, with the reason in words."""


class NotPermitted(CommandError):
    pass


class Conflict(CommandError):
    pass


class NotSwitchedOn(CommandError):
    pass


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class Outcome:
    """What a handler reports. ``state`` defaults to succeeded; a handler
    that could not learn what happened says outcome_unknown, never failed."""

    result: dict[str, Any]
    state: str = SUCCEEDED
    reason_code: str | None = None


@dataclass
class Actor:
    """Who asked and who approved, for a handler to record."""

    requested_by: int
    approved_by: int | None
    command_id: int


Handler = Callable[[AsyncSession, Any, Actor], Awaitable[Outcome]]
Check = Callable[[AsyncSession, Any], Awaitable[str | None]]
Preview = Callable[[AsyncSession, Any], Awaitable[dict[str, Any]]]


async def _always(_s: AsyncSession, _t: Any) -> str | None:
    return None


async def _no_preview(_s: AsyncSession, _t: Any) -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    request_capability: str
    target: type[Target]
    handler: Handler
    execution: Literal["inline", "worker"] = "inline"
    approve_capability: str | None = None
    feature: str | None = None
    eligible: Check = _always
    preview: Preview = _no_preview
    notes: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "request_capability": self.request_capability,
            "approve_capability": self.approve_capability,
            "requires_approval": self.approve_capability is not None,
            "execution": self.execution,
            "feature": self.feature,
            "available": self.feature is None or features.is_on(self.feature),
            "target_schema": self.target.model_json_schema(),
            "notes": list(self.notes),
        }


REGISTRY: dict[str, CommandSpec] = {}


def register(spec: CommandSpec) -> CommandSpec:
    for cap in (spec.request_capability, spec.approve_capability):
        if cap is not None and cap not in staff_roles.CAPABILITIES:
            raise KeyError(cap)
    REGISTRY[spec.name] = spec
    return spec


_LOADED = False


def load() -> dict[str, CommandSpec]:
    """Import the modules that register commands. Lazy, so the domain
    modules may import this one without a cycle."""
    global _LOADED
    if not _LOADED:
        from api.services.staff import (  # noqa: F401
            evaluations,
            incidents,
            refunds,
            role_admin,
            users,
        )

        _LOADED = True
    return REGISTRY


def catalogue(roles: set[str]) -> list[dict[str, Any]]:
    out = []
    for spec in load().values():
        row = spec.describe()
        row["can_request"] = staff_roles.can(roles, spec.request_capability)
        row["can_approve"] = bool(
            spec.approve_capability and staff_roles.can(roles, spec.approve_capability)
        )
        out.append(row)
    return out


@dataclass
class CommandView:
    id: int
    command: str
    environment: str
    target: dict[str, Any]
    reason: str
    idempotency_key: str
    state: str
    requested_by: int
    requested_roles: list[str]
    approval_required: bool
    approved_by: int | None
    preview: dict[str, Any] | None
    result: dict[str, Any] | None
    reason_code: str | None
    expires_at: str | None
    created_at: str
    finished_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def view(row: StaffCommandModel) -> CommandView:
    return CommandView(
        id=row.id,
        command=row.command,
        environment=row.environment,
        target=dict(row.target or {}),
        reason=row.reason,
        idempotency_key=row.idempotency_key,
        state=row.state,
        requested_by=row.requested_by,
        requested_roles=list(row.requested_roles or []),
        approval_required=bool(row.approval_required),
        approved_by=row.approved_by,
        preview=row.preview,
        result=row.result,
        reason_code=row.reason_code,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
        created_at=row.created_at.isoformat() if row.created_at else "",
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _fingerprint(command: str, target: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps([command, target], sort_keys=True, default=str).encode()
    ).hexdigest()


def _audit(
    session: AsyncSession,
    *,
    actor_user_id: int,
    action: str,
    row: StaffCommandModel,
    detail: str = "",
) -> None:
    """One line per step. The note names the command and its id; the target
    stays in ``staff_commands`` so the audit never copies a payload."""
    target = row.target or {}
    session.add(
        AdminActionLogModel(
            actor_user_id=actor_user_id,
            action=action,
            target_user_id=target.get("user_id")
            if isinstance(target.get("user_id"), int)
            else None,
            target_organization_id=target.get("organization_id")
            if isinstance(target.get("organization_id"), int)
            else None,
            note=f"#{row.id} {row.command} [{row.state}] {detail}"[:500],
        )
    )


def _spec(name: str) -> CommandSpec:
    spec = load().get(name)
    if spec is None:
        raise CommandError(f"There is no staff command called {name}.")
    if spec.feature and not features.is_on(spec.feature):
        raise NotSwitchedOn(f"{name} is not switched on here ({spec.feature}).")
    return spec


def _parse(spec: CommandSpec, target: dict[str, Any]) -> Target:
    try:
        return spec.target.model_validate(target)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ())) or "target"
        raise CommandError(f"{where}: {first.get('msg', 'invalid')}") from None


async def request(
    session: AsyncSession,
    *,
    ctx: staff_roles.StaffContext,
    command: str,
    target: dict[str, Any],
    reason: str,
    idempotency_key: str,
    environment: str | None = None,
) -> CommandView:
    spec = _spec(command)
    if not ctx.can(spec.request_capability):
        raise NotPermitted(
            f"Your console role does not include {spec.request_capability}."
        )
    if environment and environment != constants.ENVIRONMENT:
        raise CommandError(
            f"This console runs in {constants.ENVIRONMENT}; a command for "
            f"{environment} must be asked for there."
        )
    reason = (reason or "").strip()
    if len(reason) < 4:
        raise CommandError("Give a reason; it is recorded in the audit.")
    parsed = _parse(spec, target)
    normal = parsed.model_dump(mode="json")

    existing = (
        await session.execute(
            select(StaffCommandModel).where(
                StaffCommandModel.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        same = (
            existing.command == command
            and _fingerprint(existing.command, existing.target)
            == _fingerprint(command, normal)
            and existing.requested_by == ctx.user.id
        )
        if not same:
            raise Conflict(
                "That idempotency key was already used for a different request."
            )
        return view(existing)

    refusal = await spec.eligible(session, parsed)
    if refusal:
        raise CommandError(refusal)
    preview = await spec.preview(session, parsed)

    needs_approval = spec.approve_capability is not None
    row = StaffCommandModel(
        command=command,
        environment=constants.ENVIRONMENT,
        target=normal,
        reason=reason,
        idempotency_key=idempotency_key,
        state=AWAITING_APPROVAL if needs_approval else QUEUED,
        requested_by=ctx.user.id,
        requested_roles=sorted(ctx.roles),
        approval_required=needs_approval,
        preview=preview,
        expires_at=_now() + APPROVAL_WINDOW if needs_approval else None,
        created_at=_now(),
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        # Two requests with one key at once: the other one won; answer with it.
        winner = (
            await session.execute(
                select(StaffCommandModel).where(
                    StaffCommandModel.idempotency_key == idempotency_key
                )
            )
        ).scalar_one()
        return view(winner)

    _audit(
        session,
        actor_user_id=ctx.user.id,
        action="staff_command_requested",
        row=row,
        detail=reason,
    )
    if not needs_approval and spec.execution == "inline":
        await _run_inline(session, spec, row, parsed)
    return view(row)


async def dry_run(
    session: AsyncSession,
    *,
    ctx: staff_roles.StaffContext,
    command: str,
    target: dict[str, Any],
) -> dict[str, Any]:
    """What ``request`` would do, without writing anything: whether this
    person may ask, whether it is eligible now, and the exact preview. The
    preview is computed again at request time; this one is for reading."""
    spec = _spec(command)
    if not ctx.can(spec.request_capability):
        raise NotPermitted(
            f"Your console role does not include {spec.request_capability}."
        )
    parsed = _parse(spec, target)
    refusal = await spec.eligible(session, parsed)
    return {
        "command": command,
        "environment": constants.ENVIRONMENT,
        "roles": sorted(ctx.roles),
        "eligible": refusal is None,
        "refusal": refusal,
        "preview": None if refusal else await spec.preview(session, parsed),
        "requires_approval": spec.approve_capability is not None,
        "approve_capability": spec.approve_capability,
        "execution": spec.execution,
        "summary": spec.summary,
    }


async def _locked(session: AsyncSession, command_id: int) -> StaffCommandModel:
    row = (
        await session.execute(
            select(StaffCommandModel)
            .where(StaffCommandModel.id == command_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(command_id)
    return row


async def approve(
    session: AsyncSession, *, ctx: staff_roles.StaffContext, command_id: int
) -> CommandView:
    row = await _locked(session, command_id)
    spec = _spec(row.command)
    if row.state != AWAITING_APPROVAL:
        raise Conflict(f"This command is {row.state.replace('_', ' ')}, not waiting.")
    if row.expires_at and row.expires_at < _now():
        row.state = EXPIRED
        row.finished_at = _now()
        _audit(
            session, actor_user_id=ctx.user.id, action="staff_command_expired", row=row
        )
        raise Conflict("The approval window has passed; ask again.")
    if row.requested_by == ctx.user.id:
        raise NotPermitted("A second person must approve this; you asked for it.")
    if spec.approve_capability is None or not ctx.can(spec.approve_capability):
        raise NotPermitted(
            f"Your console role does not include {spec.approve_capability}."
        )
    parsed = _parse(spec, row.target)
    refusal = await spec.eligible(session, parsed)
    if refusal:
        row.state = FAILED
        row.reason_code = "no_longer_eligible"
        row.result = {"message": refusal}
        row.finished_at = _now()
        _audit(
            session,
            actor_user_id=ctx.user.id,
            action="staff_command_failed",
            row=row,
            detail=refusal,
        )
        return view(row)
    row.approved_by = ctx.user.id
    row.approved_at = _now()
    row.state = QUEUED
    _audit(session, actor_user_id=ctx.user.id, action="staff_command_approved", row=row)
    if spec.execution == "inline":
        await _run_inline(session, spec, row, parsed)
    return view(row)


async def reject(
    session: AsyncSession,
    *,
    ctx: staff_roles.StaffContext,
    command_id: int,
    note: str,
) -> CommandView:
    row = await _locked(session, command_id)
    spec = _spec(row.command)
    if row.state != AWAITING_APPROVAL:
        raise Conflict(f"This command is {row.state.replace('_', ' ')}, not waiting.")
    # The requester may withdraw their own; anyone else needs approve rights.
    if row.requested_by != ctx.user.id and not (
        spec.approve_capability and ctx.can(spec.approve_capability)
    ):
        raise NotPermitted("Only an approver or the requester can turn this down.")
    row.state = REJECTED
    row.finished_at = _now()
    row.result = {"note": (note or "")[:300]}
    _audit(
        session,
        actor_user_id=ctx.user.id,
        action="staff_command_rejected",
        row=row,
        detail=note or "",
    )
    return view(row)


async def _execute(
    session: AsyncSession, spec: CommandSpec, row: StaffCommandModel, parsed: Target
) -> None:
    actor = Actor(
        requested_by=row.requested_by, approved_by=row.approved_by, command_id=row.id
    )
    try:
        async with session.begin_nested():
            outcome = await spec.handler(session, parsed, actor)
    except CommandError as exc:
        outcome = Outcome({"message": str(exc)}, state=FAILED, reason_code="refused")
    except Exception as exc:  # noqa: BLE001 -- recorded, never retried blindly
        logger.exception("Staff command {} #{} raised", row.command, row.id)
        outcome = Outcome(
            {"message": "The command stopped part way; check before trying again."},
            state=OUTCOME_UNKNOWN,
            reason_code=type(exc).__name__[:64],
        )
    row.state = outcome.state
    row.result = outcome.result
    row.reason_code = outcome.reason_code
    row.finished_at = _now()
    _audit(
        session,
        actor_user_id=row.approved_by or row.requested_by,
        action=f"staff_command_{'succeeded' if outcome.state == SUCCEEDED else outcome.state}",
        row=row,
    )


async def _run_inline(
    session: AsyncSession, spec: CommandSpec, row: StaffCommandModel, parsed: Target
) -> None:
    row.state = RUNNING
    row.started_at = _now()
    await _execute(session, spec, row, parsed)


async def get(session: AsyncSession, command_id: int) -> CommandView:
    row = await session.get(StaffCommandModel, command_id)
    if row is None:
        raise LookupError(command_id)
    return view(row)


async def list_commands(
    session: AsyncSession,
    *,
    state: str | None = None,
    command_prefix: str | None = None,
    limit: int = 50,
) -> list[CommandView]:
    query = select(StaffCommandModel).order_by(StaffCommandModel.id.desc()).limit(limit)
    if state:
        query = query.where(StaffCommandModel.state == state)
    if command_prefix:
        query = query.where(StaffCommandModel.command.like(f"{command_prefix}%"))
    return [view(r) for r in (await session.execute(query)).scalars().all()]


def needs_worker(view_: CommandView) -> bool:
    spec = load().get(view_.command)
    return bool(spec and spec.execution == "worker" and view_.state == QUEUED)


async def enqueue(command_id: int) -> None:
    """Hand a queued command to the worker. A lost enqueue is picked up by
    ``sweep`` after ``REQUEUE_AFTER``; running once is the CAS in ``run``."""
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    try:
        await enqueue_job(FunctionNames.RUN_STAFF_COMMAND, command_id)
    except Exception:  # noqa: BLE001 -- the sweep re-enqueues
        logger.exception("Could not enqueue staff command #{}", command_id)


async def run(command_id: int) -> str | None:
    """The worker's half: claim queued -> running once, then execute."""
    async with db_client.async_session() as session:
        claimed = (
            await session.execute(
                update(StaffCommandModel)
                .where(
                    StaffCommandModel.id == command_id,
                    StaffCommandModel.state == QUEUED,
                )
                .values(state=RUNNING, started_at=_now())
                .returning(StaffCommandModel.id)
            )
        ).scalar_one_or_none()
        if claimed is None:
            # Another job has it, or it is not queued: nothing to do, and
            # nothing was changed that would need rolling back.
            return None
        await session.commit()

    async with db_client.async_session() as session:
        row = await session.get(StaffCommandModel, command_id)
        spec = load().get(row.command)
        if spec is None:
            row.state = FAILED
            row.reason_code = "unknown_command"
            row.finished_at = _now()
        else:
            await _execute(session, spec, row, _parse(spec, row.target))
        await session.commit()
        return row.state


async def sweep() -> dict[str, int]:
    """Expire unapproved requests, re-enqueue lost ones, and mark runs that
    never reported back as outcome unknown (never retried)."""
    now = _now()
    counts = {"expired": 0, "requeued": 0, "unknown": 0}
    async with db_client.async_session() as session:
        expired = await session.execute(
            update(StaffCommandModel)
            .where(
                StaffCommandModel.state == AWAITING_APPROVAL,
                StaffCommandModel.expires_at < now,
            )
            .values(state=EXPIRED, finished_at=now)
            .returning(StaffCommandModel.id)
        )
        counts["expired"] = len(expired.all())
        unknown = await session.execute(
            update(StaffCommandModel)
            .where(
                StaffCommandModel.state == RUNNING,
                StaffCommandModel.started_at < now - UNKNOWN_AFTER,
            )
            .values(state=OUTCOME_UNKNOWN, finished_at=now, reason_code="worker_lost")
            .returning(StaffCommandModel.id)
        )
        counts["unknown"] = len(unknown.all())
        stale = (
            await session.execute(
                select(StaffCommandModel.id, StaffCommandModel.command).where(
                    StaffCommandModel.state == QUEUED,
                    StaffCommandModel.created_at < now - REQUEUE_AFTER,
                )
            )
        ).all()
        await session.commit()
    for command_id, name in stale:
        spec = load().get(name)
        if spec and spec.execution == "worker":
            await enqueue(command_id)
            counts["requeued"] += 1
    return counts
