"""Routine operations as typed, allowlisted commands (handoff 34).

The staff console must never offer arbitrary shell, SQL or generic AWS
mutation. Everything it can do is a command defined in ``REGISTRY`` below:
a name, the minimum staff role, the environments it may run in, a pydantic
schema for its target, whether a second person must approve it, an
eligibility check, an impact preview and a handler. A request carries the
design's full contract -- typed command, role, environment, target, reason,
approval, idempotency key and result -- and is stored in ``ops_commands``
before anything happens.

The life of a request:

    requested --(no approval needed)--> queued --> running --> succeeded | failed
              \\--(approval needed)--> awaiting_approval --approve--> queued ...
                                                       \\--reject--> rejected
                                                       \\--timeout--> expired
    infrastructure commands with no runbook configured  --> needs_setup

**Accepted is queued, not succeeded.** A mutating command is handed to the
ARQ worker (``run_ops_command``) and the response says ``queued``. Only the
worker's compare-and-swap from queued to running executes it, so a command
runs once however many times it is enqueued. Read-only commands run inline:
there is nothing to make durable about reading.

**Idempotency.** The same key returns the same request. The same key with a
different command or target is a conflict, never a silent second action.

**Scope.** A command that touches one workspace names it in its target, and
its handler checks the row it changes belongs there (``api/AGENTS.md``,
Organization Scoping).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.models import AdminActionLogModel, UserModel
from api.db.ops_models import OpsCommandModel
from api.enums import STAFF_ROLE_RANK, StaffRole
from api.services.ops import redaction

REQUESTED = "requested"
AWAITING_APPROVAL = "awaiting_approval"
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
REJECTED = "rejected"
EXPIRED = "expired"
NEEDS_SETUP = "needs_setup"
#: A command that started and never reported back (the worker died mid-run).
#: Not retried blindly: a person checks what happened and decides.
OUTCOME_UNKNOWN = "outcome_unknown"
TERMINAL = (SUCCEEDED, FAILED, REJECTED, EXPIRED, NEEDS_SETUP, OUTCOME_UNKNOWN)

#: A queued command not picked up in this long is enqueued again (the queue
#: write may have been lost); running once is still guaranteed by the
#: compare-and-swap in ``execute``.
REQUEUE_AFTER = timedelta(minutes=2)
#: A synchronous command running this long is marked outcome_unknown.
UNKNOWN_AFTER = timedelta(minutes=15)

APPROVAL_WINDOW = timedelta(hours=1)
ANY_ENVIRONMENT = ("*",)


class CommandError(ValueError):
    """A request that cannot be accepted, with the reason."""


class NotPermitted(CommandError):
    pass


class Conflict(CommandError):
    pass


class _Target(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoTarget(_Target):
    pass


class DeliveryTarget(_Target):
    organization_id: int = Field(gt=0)
    delivery_id: int = Field(gt=0)


class ProviderTarget(_Target):
    component: Literal["stt", "llm", "tts", "data"]
    provider: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    #: A pause lifts itself after this long; None means until resumed.
    expires_in_minutes: int | None = Field(default=None, ge=5, le=7 * 24 * 60)


class AgentTarget(_Target):
    organization_id: int = Field(gt=0)
    workflow_id: int = Field(gt=0)
    expires_in_minutes: int | None = Field(default=None, ge=5, le=7 * 24 * 60)


class RoutineTarget(_Target):
    organization_id: int = Field(gt=0)
    routine_id: int = Field(gt=0)


class FlagTarget(_Target):
    feature: str = Field(min_length=1, max_length=64)
    organization_id: int | None = Field(default=None, gt=0)
    enabled: bool
    expires_in_minutes: int | None = Field(default=None, ge=5, le=30 * 24 * 60)


class RollbackTarget(_Target):
    command_id: int = Field(gt=0)


class CostStopTarget(_Target):
    scope: Literal["platform", "organization"]
    organization_id: int | None = Field(default=None, gt=0)
    expires_in_minutes: int | None = Field(default=None, ge=5, le=7 * 24 * 60)


class WorkerTarget(_Target):
    #: Which process group; the runbook decides what that means on the box.
    group: Literal["api", "worker", "all"] = "worker"
    #: Bounded impact: the runbook waits at most this long for active calls.
    drain_timeout_seconds: int = Field(default=300, ge=30, le=1800)


Handler = Callable[[AsyncSession, Any, OpsCommandModel], Awaitable[dict[str, Any]]]
Check = Callable[[AsyncSession, Any], Awaitable[str | None]]
Preview = Callable[[AsyncSession, Any], Awaitable[dict[str, Any]]]


async def _always(_session: AsyncSession, _target: Any) -> str | None:
    return None


async def _no_preview(_session: AsyncSession, _target: Any) -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    kind: Literal["read", "mutate", "infrastructure"]
    min_role: StaffRole
    target: type[_Target]
    handler: Handler
    requires_approval: bool = False
    environments: tuple[str, ...] = ANY_ENVIRONMENT
    eligible: Check = _always
    preview: Preview = _no_preview
    rollback_of: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "kind": self.kind,
            "min_role": self.min_role.value,
            "requires_approval": self.requires_approval,
            "environments": list(self.environments),
            "target_schema": self.target.model_json_schema(),
            "notes": list(self.notes),
        }


# --- handlers ---------------------------------------------------------------


async def _queue_inspect(
    session: AsyncSession, _target: NoTarget, _row
) -> dict[str, Any]:
    from api.services.ops import infra_health, telemetry

    queue = await infra_health._timed("queue", infra_health._queue)
    worker = await infra_health._timed("worker", infra_health._worker)
    return {
        "queue": queue.as_dict(),
        "worker": worker.as_dict(),
        "analytics_outbox": await telemetry.outbox_health(session),
    }


async def _delivery_row(session: AsyncSession, target: DeliveryTarget):
    from api.db.models import WebhookDeliveryModel

    return await session.scalar(
        select(WebhookDeliveryModel).where(
            WebhookDeliveryModel.id == target.delivery_id,
            WebhookDeliveryModel.organization_id == target.organization_id,
        )
    )


def _delivery_view(row) -> dict[str, Any]:
    return {
        "delivery_id": row.id,
        "organization_id": row.organization_id,
        "workflow_run_id": row.workflow_run_id,
        "status": row.status,
        "attempt_count": row.attempt_count,
        "max_attempts": row.max_attempts,
        "last_status_code": row.last_status_code,
        # A normalized code; the raw error can carry the endpoint's response.
        "last_error_code": redaction.reason_code(row.last_error)
        if row.last_error
        else None,
        "scheduled_for": row.scheduled_for.isoformat() if row.scheduled_for else None,
    }


async def _delivery_inspect(
    session: AsyncSession, target: DeliveryTarget, _row
) -> dict[str, Any]:
    row = await _delivery_row(session, target)
    if row is None:
        raise CommandError("No such delivery in that workspace.")
    return _delivery_view(row)


async def _delivery_retry_eligible(
    session: AsyncSession, target: DeliveryTarget
) -> str | None:
    row = await _delivery_row(session, target)
    if row is None:
        return "No such delivery in that workspace."
    if row.status != "dead_letter":
        return (
            f"Only a dead-lettered delivery can be retried; this one is {row.status}."
        )
    return None


async def _delivery_retry(
    session: AsyncSession, target: DeliveryTarget, command
) -> dict[str, Any]:
    from api.tasks.webhook_delivery import _enqueue_delivery

    row = await _delivery_row(session, target)
    if row is None or row.status != "dead_letter":
        # Re-checked at execution: it may have changed since the request.
        raise CommandError("The delivery is no longer dead-lettered.")
    row.status = "pending"
    row.max_attempts = int(row.attempt_count or 0) + 3
    row.scheduled_for = datetime.now(UTC)
    await session.flush()
    await _enqueue_delivery(
        row.id, int(row.attempt_count or 0), reclaim_token=command.id
    )
    return {"retried": True, **_delivery_view(row)}


async def _provider_preview(
    session: AsyncSession, target: ProviderTarget
) -> dict[str, Any]:
    from api.services.configuration import platform_credentials

    row = await platform_credentials.active_credential(
        session, component=target.component, provider=target.provider
    )
    return {
        "currently_active": row is not None and bool(row.is_active),
        "impact": (
            f"Managed {target.component} on {target.provider} stops being offered and "
            "calls already running keep their key. Workspaces on their own keys are "
            "unaffected."
        ),
        "lifts_itself_after_minutes": target.expires_in_minutes,
    }


async def _provider_set(
    session: AsyncSession, target: ProviderTarget, active: bool
) -> dict[str, Any]:
    from api.services.configuration import platform_credentials

    try:
        view = await platform_credentials.set_active(
            session,
            component=target.component,
            provider=target.provider,
            is_active=active,
        )
    except platform_credentials.PlatformCredentialError as exc:
        raise CommandError(str(exc)) from None
    await session.flush()
    return {
        "component": view.component,
        "provider": view.provider,
        "is_active": view.is_active,
    }


async def _provider_pause(session, target: ProviderTarget, _row) -> dict[str, Any]:
    return await _provider_set(session, target, False)


async def _provider_resume(session, target: ProviderTarget, _row) -> dict[str, Any]:
    return await _provider_set(session, target, True)


async def _agent_row(session: AsyncSession, target: AgentTarget):
    from api.db.models import WorkflowModel

    return await session.scalar(
        select(WorkflowModel).where(
            WorkflowModel.id == target.workflow_id,
            WorkflowModel.organization_id == target.organization_id,
        )
    )


async def _agent_eligible(session: AsyncSession, target: AgentTarget) -> str | None:
    return (
        None
        if await _agent_row(session, target)
        else "No such agent in that workspace."
    )


async def _agent_preview(session: AsyncSession, target: AgentTarget) -> dict[str, Any]:
    row = await _agent_row(session, target)
    return {
        "agent": row.name if row else None,
        "currently_live": bool(row.is_live) if row else None,
        "impact": "The agent stops taking calls and runs; it stays visible and editable.",
        "lifts_itself_after_minutes": target.expires_in_minutes,
    }


async def _agent_set(session, target: AgentTarget, live: bool) -> dict[str, Any]:
    row = await _agent_row(session, target)
    if row is None:
        raise CommandError("No such agent in that workspace.")
    previous = bool(row.is_live)
    row.is_live = live
    await session.flush()
    return {"workflow_id": row.id, "is_live": live, "previous_is_live": previous}


async def _agent_pause(session, target: AgentTarget, _row) -> dict[str, Any]:
    return await _agent_set(session, target, False)


async def _agent_resume(session, target: AgentTarget, _row) -> dict[str, Any]:
    return await _agent_set(session, target, True)


async def _routine_row(session: AsyncSession, target: RoutineTarget):
    from api.db.models import AgentRoutineModel

    return await session.scalar(
        select(AgentRoutineModel).where(
            AgentRoutineModel.id == target.routine_id,
            AgentRoutineModel.organization_id == target.organization_id,
        )
    )


async def _routine_eligible(session: AsyncSession, target: RoutineTarget) -> str | None:
    return (
        None
        if await _routine_row(session, target)
        else "No such routine in that workspace."
    )


async def _routine_set(session, target: RoutineTarget, active: bool) -> dict[str, Any]:
    row = await _routine_row(session, target)
    if row is None:
        raise CommandError("No such routine in that workspace.")
    if active and row.tested_at is None:
        raise CommandError(
            "This routine has never been test-run, so it may not be armed."
        )
    previous = bool(row.is_active)
    row.is_active = active
    await session.flush()
    return {"routine_id": row.id, "is_active": active, "previous_is_active": previous}


async def _routine_pause(session, target: RoutineTarget, _row) -> dict[str, Any]:
    return await _routine_set(session, target, False)


async def _routine_resume(session, target: RoutineTarget, _row) -> dict[str, Any]:
    return await _routine_set(session, target, True)


async def _flag_eligible(_session: AsyncSession, target: FlagTarget) -> str | None:
    from api.services import features

    return (
        None
        if target.feature in features.FLAGS
        else f"Unknown feature {target.feature}."
    )


async def _flag_preview(_session: AsyncSession, target: FlagTarget) -> dict[str, Any]:
    from api.services import features

    if target.feature not in features.FLAGS:
        return {}
    current = features.is_on(target.feature, target.organization_id)
    return {
        "feature": target.feature,
        "description": features.describe(target.feature),
        "scope": "everyone"
        if target.organization_id is None
        else f"workspace {target.organization_id}",
        "currently_on": current,
        "will_be_on": target.enabled,
        "changes_anything": current != target.enabled,
    }


async def _flag_set(
    session: AsyncSession, target: FlagTarget, command
) -> dict[str, Any]:
    from api.db.feature_override_models import FeatureOverrideModel
    from api.services import feature_admin

    condition = (
        FeatureOverrideModel.organization_id.is_(None)
        if target.organization_id is None
        else FeatureOverrideModel.organization_id == target.organization_id
    )
    existing = await session.scalar(
        select(FeatureOverrideModel).where(
            FeatureOverrideModel.feature == target.feature, condition
        )
    )
    previous = (
        None
        if existing is None
        else {
            "enabled": bool(existing.enabled),
            "expires_at": existing.expires_at.isoformat()
            if existing.expires_at
            else None,
            "note": existing.note,
        }
    )
    expires_at = (
        datetime.now(UTC) + timedelta(minutes=target.expires_in_minutes)
        if target.expires_in_minutes
        else None
    )
    await feature_admin.set_override(
        session,
        name=target.feature,
        organization_id=target.organization_id,
        enabled=target.enabled,
        note=f"ops command {command.id}: {command.reason}"[:300],
        expires_at=expires_at,
        actor_user_id=command.approved_by or command.requested_by,
    )
    await session.flush()
    # The previous row is the version this change can be rolled back to.
    return {
        "feature": target.feature,
        "organization_id": target.organization_id,
        "enabled": target.enabled,
        "previous": previous,
    }


async def _flag_rollback_eligible(
    session: AsyncSession, target: RollbackTarget
) -> str | None:
    row = await session.get(OpsCommandModel, target.command_id)
    if row is None or row.command not in ("flag.set", "laya.rollback", "laya.restore"):
        return "Only a flag change can be rolled back this way."
    if row.state != SUCCEEDED:
        return f"That change is {row.state}; only a change that ran can be rolled back."
    return None


async def _flag_rollback(
    session: AsyncSession, target: RollbackTarget, command
) -> dict[str, Any]:
    from api.services import feature_admin

    original = await session.get(OpsCommandModel, target.command_id)
    if original is None or original.state != SUCCEEDED:
        raise CommandError("That change cannot be rolled back.")
    result = original.result or {}
    feature = result.get("feature")
    organization_id = result.get("organization_id")
    previous = result.get("previous")
    actor = command.approved_by or command.requested_by
    if previous is None:
        await feature_admin.clear_override(
            session, name=feature, organization_id=organization_id, actor_user_id=actor
        )
    else:
        expires = previous.get("expires_at")
        await feature_admin.set_override(
            session,
            name=feature,
            organization_id=organization_id,
            enabled=bool(previous["enabled"]),
            note=f"rollback of ops command {original.id}",
            expires_at=datetime.fromisoformat(expires) if expires else None,
            actor_user_id=actor,
        )
    await session.flush()
    return {"rolled_back": original.id, "feature": feature, "restored": previous}


def _laya_flag(enabled: bool) -> Handler:
    async def handler(
        session: AsyncSession, _target: NoTarget, command
    ) -> dict[str, Any]:
        return await _flag_set(
            session, FlagTarget(feature="laya_rollback", enabled=enabled), command
        )

    return handler


async def _cost_stop_check(
    _session: AsyncSession, target: CostStopTarget
) -> str | None:
    if target.scope == "organization" and target.organization_id is None:
        return "An organization stop names the organization."
    return None


async def _cost_stop_engage(session, target: CostStopTarget, command) -> dict[str, Any]:
    from api.services.ops import cost_stop, telemetry

    expires = (
        datetime.now(UTC) + timedelta(minutes=target.expires_in_minutes)
        if target.expires_in_minutes
        else None
    )
    stop = await cost_stop.engage(
        scope=target.scope,
        organization_id=target.organization_id,
        reason=command.reason,
        engaged_by=f"user:{command.requested_by}",
        expires_at=expires,
    )
    await telemetry.record(
        session,
        "cost_stop_engaged",
        workspace_id=target.organization_id,
        properties={"scope": target.scope, "reason_code": "cost_stopped"},
    )
    return stop.as_dict()


async def _cost_stop_release(
    session, target: CostStopTarget, _command
) -> dict[str, Any]:
    from api.services.ops import cost_stop, telemetry

    removed = await cost_stop.release(
        scope=target.scope, organization_id=target.organization_id
    )
    await telemetry.record(
        session,
        "cost_stop_released",
        workspace_id=target.organization_id,
        properties={"scope": target.scope},
    )
    return {
        "released": removed,
        "scope": target.scope,
        "organization_id": target.organization_id,
    }


def _runbook(name: str) -> Handler:
    async def handler(
        _session: AsyncSession, target: WorkerTarget, command
    ) -> dict[str, Any]:
        from api.services.ops import infrastructure

        started = await infrastructure.get_runner().start(
            name,
            {
                "Group": [target.group],
                "DrainTimeoutSeconds": [str(target.drain_timeout_seconds)],
                "Reason": [f"ops command {command.id}"],
            },
        )
        if not started.started:
            raise NeedsSetup(started.detail)
        return {
            "automation_execution_id": started.execution_id,
            "detail": started.detail,
            "async": True,
        }

    return handler


class NeedsSetup(CommandError):
    """The command is allowed but the infrastructure it drives is not set up."""


async def _backups_status(
    session: AsyncSession, _target: NoTarget, _row
) -> dict[str, Any]:
    from api.services.backup.database import last_successful
    from api.services.ops import evidence

    return {
        "last_backup": await last_successful(),
        "last_restore_drill": (
            found.as_dict()
            if (found := await evidence.latest(session, "restore_drill"))
            else None
        ),
    }


async def _deployments_status(
    session: AsyncSession, _target: NoTarget, _row
) -> dict[str, Any]:
    from api.services.ops import evidence
    from api.services.system_status import build_info

    return {
        "running": build_info(),
        "environment": constants.ENVIRONMENT,
        "last_deployment": (
            found.as_dict()
            if (found := await evidence.latest(session, "deployment"))
            else None
        ),
        "last_capacity_review": (
            found.as_dict()
            if (found := await evidence.latest(session, "capacity_review"))
            else None
        ),
    }


_S = StaffRole.SUPPORT
_A = StaffRole.SUPERADMIN

REGISTRY: dict[str, CommandSpec] = {
    spec.name: spec
    for spec in (
        CommandSpec(
            "queue.inspect",
            "Queue depth, oldest waiting job, worker heartbeat and analytics backlog.",
            "read",
            _S,
            NoTarget,
            _queue_inspect,
        ),
        CommandSpec(
            "delivery.inspect",
            "One webhook delivery's state, attempts and normalized last error.",
            "read",
            _S,
            DeliveryTarget,
            _delivery_inspect,
        ),
        CommandSpec(
            "delivery.retry",
            "Retry a dead-lettered webhook delivery (three more attempts).",
            "mutate",
            _S,
            DeliveryTarget,
            _delivery_retry,
            eligible=_delivery_retry_eligible,
        ),
        CommandSpec(
            "provider.pause",
            "Stop offering one managed provider; optionally lifts itself.",
            "mutate",
            _A,
            ProviderTarget,
            _provider_pause,
            requires_approval=True,
            preview=_provider_preview,
        ),
        CommandSpec(
            "provider.resume",
            "Offer a paused managed provider again.",
            "mutate",
            _A,
            ProviderTarget,
            _provider_resume,
            preview=_provider_preview,
        ),
        CommandSpec(
            "agent.pause",
            "Stop one agent taking calls and runs; optionally lifts itself.",
            "mutate",
            _S,
            AgentTarget,
            _agent_pause,
            eligible=_agent_eligible,
            preview=_agent_preview,
        ),
        CommandSpec(
            "agent.resume",
            "Let a paused agent take calls and runs again.",
            "mutate",
            _S,
            AgentTarget,
            _agent_resume,
            eligible=_agent_eligible,
            preview=_agent_preview,
        ),
        CommandSpec(
            "routine.pause",
            "Disarm one routine.",
            "mutate",
            _S,
            RoutineTarget,
            _routine_pause,
            eligible=_routine_eligible,
        ),
        CommandSpec(
            "routine.resume",
            "Re-arm a routine that has been test-run.",
            "mutate",
            _S,
            RoutineTarget,
            _routine_resume,
            eligible=_routine_eligible,
        ),
        CommandSpec(
            "flag.set",
            "Switch a feature for one workspace or everyone (versioned; can be rolled back).",
            "mutate",
            _A,
            FlagTarget,
            _flag_set,
            requires_approval=True,
            eligible=_flag_eligible,
            preview=_flag_preview,
        ),
        CommandSpec(
            "flag.rollback",
            "Put a flag back to the version before a flag.set ran.",
            "mutate",
            _A,
            RollbackTarget,
            _flag_rollback,
            eligible=_flag_rollback_eligible,
            rollback_of="flag.set",
        ),
        CommandSpec(
            "laya.rollback",
            "Route by rules alone; Laya is never asked.",
            "mutate",
            _S,
            NoTarget,
            _laya_flag(True),
            notes=("Safe direction: no approval, so it is fast in an incident.",),
        ),
        CommandSpec(
            "laya.restore",
            "Let Auto ask Laya again, as LAYA_ROUTING says.",
            "mutate",
            _A,
            NoTarget,
            _laya_flag(False),
            requires_approval=True,
        ),
        CommandSpec(
            "cost_stop.engage",
            "Refuse new billable work, platform-wide or for one workspace.",
            "mutate",
            _S,
            CostStopTarget,
            _cost_stop_engage,
            eligible=_cost_stop_check,
            notes=("Safe direction: no approval; work already running finishes.",),
        ),
        CommandSpec(
            "cost_stop.release",
            "Allow new billable work again.",
            "mutate",
            _A,
            CostStopTarget,
            _cost_stop_release,
            requires_approval=True,
            eligible=_cost_stop_check,
        ),
        CommandSpec(
            "workers.drain",
            "Approved runbook: stop new calls, finish active ones, report progress.",
            "infrastructure",
            _A,
            WorkerTarget,
            _runbook("workers.drain"),
            requires_approval=True,
            notes=("Runs the SSM Automation document mapped in OPS_SSM_DOCUMENTS.",),
        ),
        CommandSpec(
            "workers.restart",
            "Approved runbook: drain, restart, health-check, report.",
            "infrastructure",
            _A,
            WorkerTarget,
            _runbook("workers.restart"),
            requires_approval=True,
            notes=("Runs the SSM Automation document mapped in OPS_SSM_DOCUMENTS.",),
        ),
        CommandSpec(
            "backups.status",
            "Last successful backup and last restore drill.",
            "read",
            _S,
            NoTarget,
            _backups_status,
        ),
        CommandSpec(
            "deployments.status",
            "Running build, last deployment and last capacity review.",
            "read",
            _S,
            NoTarget,
            _deployments_status,
        ),
    )
}


# --- the request lifecycle ---------------------------------------------------


@dataclass(frozen=True)
class CommandView:
    id: int
    command: str
    environment: str
    target: dict[str, Any]
    reason: str
    idempotency_key: str
    state: str
    requested_by: int | None
    requested_role: str | None
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


def _view(row: OpsCommandModel) -> CommandView:
    return CommandView(
        id=row.id,
        command=row.command,
        environment=row.environment,
        target=dict(row.target or {}),
        reason=row.reason,
        idempotency_key=row.idempotency_key,
        state=row.state,
        requested_by=row.requested_by,
        requested_role=row.requested_role,
        approval_required=bool(row.approval_required),
        approved_by=row.approved_by,
        preview=row.preview,
        result=row.result,
        reason_code=row.reason_code,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
        created_at=row.created_at.isoformat() if row.created_at else "",
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
    )


def _rank(user: UserModel) -> int:
    return STAFF_ROLE_RANK.get(getattr(user, "staff_role", None) or "", -1)


def _audit(
    session: AsyncSession, *, actor: int, action: str, row: OpsCommandModel
) -> None:
    session.add(
        AdminActionLogModel(
            actor_user_id=actor,
            action=action,
            target_organization_id=(row.target or {}).get("organization_id"),
            note=(
                f"ops_command={row.id}; command={row.command}; env={row.environment}; "
                f"state={row.state}; target={json.dumps(row.target, sort_keys=True)}; "
                f"reason={row.reason}"
            )[:500],
        )
    )


def catalogue(user: UserModel | None = None) -> list[dict[str, Any]]:
    """Every command, and whether this user may request it here."""
    out = []
    for spec in REGISTRY.values():
        row = spec.describe()
        row["available_here"] = _environment_allows(spec)
        if user is not None:
            row["permitted"] = _rank(user) >= STAFF_ROLE_RANK[spec.min_role.value]
        out.append(row)
    return out


def _environment_allows(spec: CommandSpec) -> bool:
    return (
        spec.environments == ANY_ENVIRONMENT
        or constants.ENVIRONMENT in spec.environments
    )


Enqueue = Callable[[int], Awaitable[None]]


async def _enqueue_default(command_id: int) -> None:
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    await enqueue_job(
        FunctionNames.RUN_OPS_COMMAND, command_id, _job_id=f"ops-command-{command_id}"
    )


async def request(
    session: AsyncSession,
    *,
    user: UserModel,
    command: str,
    target: dict[str, Any] | None,
    reason: str,
    idempotency_key: str,
    environment: str | None = None,
    enqueue: Enqueue | None = None,
) -> CommandView:
    """Accept (or refuse) one command. The caller commits."""
    spec = REGISTRY.get(command)
    if spec is None:
        raise CommandError(f"Unknown command {command!r}. Commands are allowlisted.")
    if _rank(user) < STAFF_ROLE_RANK[spec.min_role.value]:
        raise NotPermitted(f"{command} needs the {spec.min_role.value} role.")
    environment = environment or constants.ENVIRONMENT
    if environment != constants.ENVIRONMENT:
        raise NotPermitted(
            f"This console runs in {constants.ENVIRONMENT}; a command for {environment} "
            "must be requested from that environment's console."
        )
    if not _environment_allows(spec):
        raise NotPermitted(f"{command} is not allowed in {environment}.")
    reason = (reason or "").strip()
    if len(reason) < 4:
        raise CommandError("Give a reason; it is kept with the audit record.")
    key = (idempotency_key or "").strip()
    if not (8 <= len(key) <= 128):
        raise CommandError("An idempotency key of 8 to 128 characters is required.")
    try:
        parsed = spec.target.model_validate(target or {})
    except ValidationError as exc:
        raise CommandError(
            "Invalid target: "
            + "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
            )
        ) from None
    target_json = parsed.model_dump(mode="json")

    existing = await session.scalar(
        select(OpsCommandModel).where(OpsCommandModel.idempotency_key == key)
    )
    if existing is not None:
        if existing.command != command or (existing.target or {}) != target_json:
            raise Conflict(
                "That idempotency key was already used for a different command or target."
            )
        return _view(existing)

    blocked = await spec.eligible(session, parsed)
    if blocked:
        raise CommandError(blocked)
    preview = await spec.preview(session, parsed)

    now = datetime.now(UTC)
    row = OpsCommandModel(
        command=command,
        environment=environment,
        target=target_json,
        reason=redaction.redact_text(reason)[:500],
        idempotency_key=key,
        state=REQUESTED,
        requested_by=user.id,
        requested_role=getattr(user, "staff_role", None),
        approval_required=spec.requires_approval,
        preview=preview or None,
        created_at=now,
    )
    if getattr(parsed, "expires_in_minutes", None):
        row.expires_at = now + timedelta(minutes=parsed.expires_in_minutes)
    session.add(row)
    await session.flush()

    if spec.requires_approval:
        row.state = AWAITING_APPROVAL
        # The approval window, separate from the effect's own expiry: an
        # unapproved request does not wait for ever.
        row.decided_at = None
        row.preview = {
            **(row.preview or {}),
            "approve_before": (now + APPROVAL_WINDOW).isoformat(),
        }
        _audit(session, actor=user.id, action="ops_command_requested", row=row)
        await session.flush()
        return _view(row)

    _audit(session, actor=user.id, action="ops_command_requested", row=row)
    if spec.kind == "read":
        row.state = QUEUED
        await session.flush()
        await execute(session, row.id)
        return _view(row)
    row.state = QUEUED
    await session.flush()
    # Read before the commit: committing expires the row, and reading an
    # expired attribute outside a greenlet is an error on the real engine.
    view = _view(row)
    await session.commit()
    await (enqueue or _enqueue_default)(view.id)
    return view


async def approve(
    session: AsyncSession,
    *,
    user: UserModel,
    command_id: int,
    enqueue: Enqueue | None = None,
) -> CommandView:
    row = await session.get(OpsCommandModel, command_id, with_for_update=True)
    if row is None:
        raise LookupError(command_id)
    spec = REGISTRY[row.command]
    if row.state != AWAITING_APPROVAL:
        raise CommandError(
            f"Command {command_id} is {row.state}, not awaiting approval."
        )
    if row.requested_by == user.id:
        raise NotPermitted("A second person approves; you requested this one.")
    if _rank(user) < STAFF_ROLE_RANK[spec.min_role.value]:
        raise NotPermitted(
            f"Approving {row.command} needs the {spec.min_role.value} role."
        )
    deadline = (row.preview or {}).get("approve_before")
    if deadline and datetime.fromisoformat(deadline) < datetime.now(UTC):
        row.state = EXPIRED
        row.finished_at = datetime.now(UTC)
        await session.flush()
        raise CommandError("The approval window has passed; request it again.")
    row.approved_by = user.id
    row.decided_at = datetime.now(UTC)
    row.state = QUEUED
    _audit(session, actor=user.id, action="ops_command_approved", row=row)
    await session.flush()
    view = _view(row)
    await session.commit()
    await (enqueue or _enqueue_default)(view.id)
    return view


async def reject(
    session: AsyncSession, *, user: UserModel, command_id: int, note: str
) -> CommandView:
    row = await session.get(OpsCommandModel, command_id, with_for_update=True)
    if row is None:
        raise LookupError(command_id)
    if row.state != AWAITING_APPROVAL:
        raise CommandError(
            f"Command {command_id} is {row.state}, not awaiting approval."
        )
    if _rank(user) < STAFF_ROLE_RANK[REGISTRY[row.command].min_role.value]:
        raise NotPermitted("Rejecting needs the same role as approving.")
    row.state = REJECTED
    row.approved_by = user.id
    row.decided_at = datetime.now(UTC)
    row.finished_at = row.decided_at
    row.result = {"note": redaction.redact_text((note or "").strip())[:300]}
    _audit(session, actor=user.id, action="ops_command_rejected", row=row)
    await session.flush()
    return _view(row)


async def execute(session: AsyncSession, command_id: int) -> CommandView | None:
    """Run a queued command once. Called by the ARQ task (and inline for
    reads). The compare-and-swap from queued to running is what makes a
    duplicate enqueue harmless."""
    claimed = await session.execute(
        update(OpsCommandModel)
        .where(OpsCommandModel.id == command_id, OpsCommandModel.state == QUEUED)
        .values(state=RUNNING, started_at=datetime.now(UTC))
    )
    if not claimed.rowcount:
        return None
    row = await session.get(OpsCommandModel, command_id, populate_existing=True)
    spec = REGISTRY.get(row.command)
    actor = row.approved_by or row.requested_by
    try:
        if spec is None:
            raise CommandError("The command is no longer in the registry.")
        target = spec.target.model_validate(row.target or {})
        result = await spec.handler(session, target, row)
    except NeedsSetup as exc:
        row.state = NEEDS_SETUP
        row.reason_code = "not_configured"
        row.result = {"detail": str(exc)}
    except CommandError as exc:
        row.state = FAILED
        row.reason_code = redaction.reason_code(str(exc))
        row.result = {"detail": str(exc)[:300]}
    except Exception as exc:  # noqa: BLE001 - the row must reach a final state
        logger.exception("Ops command {} ({}) failed", row.id, row.command)
        row.state = FAILED
        row.reason_code = redaction.reason_code(exc)
        row.result = {"detail": f"{type(exc).__name__} while running {row.command}."}
    else:
        if result.get("async"):
            # Started elsewhere (an SSM runbook); ``refresh`` finishes it.
            row.state = RUNNING
        else:
            row.state = SUCCEEDED
        row.result = result
        row.reason_code = None
    if row.state != RUNNING:
        row.finished_at = datetime.now(UTC)
    if actor is not None:
        _audit(session, actor=actor, action=f"ops_command_{row.state}", row=row)
    await _announce(session, row)
    await session.flush()
    return _view(row)


async def _announce(session: AsyncSession, row: OpsCommandModel) -> None:
    from api.services.ops import telemetry

    if REGISTRY.get(row.command) and REGISTRY[row.command].kind == "read":
        return
    await telemetry.record(
        session,
        "ops_command_executed",
        workspace_id=(row.target or {}).get("organization_id"),
        task_id=f"ops-{row.id}",
        properties={
            "status": row.state,
            "reason_code": row.reason_code,
            "command": row.command,
        },
    )


#: What every worker must be told once a command's change is committed. Run
#: after the commit, never before: a worker told early re-reads the old
#: value and keeps it until its next periodic refresh.
_FLAG_COMMANDS = frozenset(
    {"flag.set", "flag.rollback", "laya.rollback", "laya.restore"}
)
_PROVIDER_COMMANDS = frozenset({"provider.pause", "provider.resume"})


async def after_commit(view: CommandView) -> None:
    """Announce a committed change to every process. Never raises."""
    if view.state != SUCCEEDED:
        return
    try:
        if view.command in _FLAG_COMMANDS:
            await _publish(
                "feature_overrides", (view.result or {}).get("organization_id")
            )
        elif view.command in _PROVIDER_COMMANDS:
            from api.services.configuration.managed_tiers import (
                refresh_overrides as refresh_managed_tier_overrides,
            )

            await refresh_managed_tier_overrides()
            await _publish("managed_tiers", None)
    except Exception as exc:  # noqa: BLE001 - the change is committed already
        logger.warning("Could not announce ops command {}: {}", view.id, exc)


async def _publish(event_type: str, organization_id: int | None) -> None:
    """Reload this process, then tell the others: through the worker sync
    manager in an API worker, or straight onto its Redis channel from the
    ARQ worker, which has no manager."""
    from api.enums import RedisChannel
    from api.services import features
    from api.services.worker_sync.manager import get_worker_sync_manager
    from api.services.worker_sync.protocol import WorkerSyncEvent

    if event_type == "feature_overrides":
        await features.refresh_overrides()
    try:
        manager = get_worker_sync_manager()
    except RuntimeError:
        manager = None
    if manager is not None:
        await manager.broadcast(event_type, "update", org_id=str(organization_id or ""))
        return
    import redis.asyncio as aioredis

    client = aioredis.from_url(constants.REDIS_URL)
    try:
        event = WorkerSyncEvent(
            event_type=event_type, action="update", org_id=str(organization_id or "")
        )
        await client.publish(RedisChannel.WORKER_SYNC.value, event.to_json())
    finally:
        await client.aclose()


async def refresh(
    session: AsyncSession, command_id: int, *, runner=None
) -> CommandView:
    """Bring a running infrastructure command up to date from SSM."""
    from api.services.ops import infrastructure

    row = await session.get(OpsCommandModel, command_id, with_for_update=True)
    if row is None:
        raise LookupError(command_id)
    execution_id = (row.result or {}).get("automation_execution_id")
    if row.state != RUNNING or not execution_id:
        return _view(row)
    status = await (runner or infrastructure.get_runner()).status(execution_id)
    row.result = {**(row.result or {}), "progress": status}
    final = infrastructure.TERMINAL.get(str(status.get("status")))
    if final:
        row.state = final
        row.finished_at = datetime.now(UTC)
        row.reason_code = None if final == SUCCEEDED else "provider_unavailable"
    await session.flush()
    return _view(row)


async def expire_and_lift(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    enqueue: Enqueue | None = None,
) -> dict[str, int]:
    """The sweep: expire unapproved requests past their window, re-enqueue
    queued commands the worker never picked up, mark commands that started
    and never finished as outcome_unknown, and lift pauses whose own expiry
    has passed (by running the matching resume as a system command). Run by
    an ARQ cron."""
    now = now or datetime.now(UTC)
    requeue: list[int] = []
    for row in (
        await session.scalars(
            select(OpsCommandModel).where(
                OpsCommandModel.state == QUEUED,
                func.coalesce(OpsCommandModel.decided_at, OpsCommandModel.created_at)
                <= now - REQUEUE_AFTER,
            )
        )
    ).all():
        requeue.append(row.id)
    unknown = 0
    for row in (
        await session.scalars(
            select(OpsCommandModel).where(
                OpsCommandModel.state == RUNNING,
                OpsCommandModel.started_at <= now - UNKNOWN_AFTER,
            )
        )
    ).all():
        if (row.result or {}).get("async"):
            continue  # an SSM runbook: ``refresh`` follows it
        row.state = OUTCOME_UNKNOWN
        row.reason_code = "timeout"
        row.finished_at = now
        unknown += 1
    expired = 0
    for row in (
        await session.scalars(
            select(OpsCommandModel).where(OpsCommandModel.state == AWAITING_APPROVAL)
        )
    ).all():
        deadline = (row.preview or {}).get("approve_before")
        if deadline and datetime.fromisoformat(deadline) < now:
            row.state = EXPIRED
            row.finished_at = now
            expired += 1
    lifted = 0
    announce: list[CommandView] = []
    lifts = {"provider.pause": "provider.resume", "agent.pause": "agent.resume"}
    for row in (
        await session.scalars(
            select(OpsCommandModel).where(
                OpsCommandModel.state == SUCCEEDED,
                OpsCommandModel.command.in_(list(lifts)),
                OpsCommandModel.expires_at.isnot(None),
                OpsCommandModel.expires_at <= now,
            )
        )
    ).all():
        key = f"lift-{row.id}"
        if await session.scalar(
            select(OpsCommandModel.id).where(OpsCommandModel.idempotency_key == key)
        ):
            continue
        target = {
            k: v for k, v in (row.target or {}).items() if k != "expires_in_minutes"
        }
        target["expires_in_minutes"] = None
        lift = OpsCommandModel(
            command=lifts[row.command],
            environment=row.environment,
            target=target,
            reason=f"Pause from command {row.id} expired.",
            idempotency_key=key,
            state=QUEUED,
            requested_by=row.requested_by,
            requested_role="system",
            approval_required=False,
            created_at=now,
        )
        session.add(lift)
        await session.flush()
        done = await execute(session, lift.id)
        if done is not None:
            announce.append(done)
        lifted += 1
    await session.commit()
    for view in announce:
        await after_commit(view)
    for command_id in requeue:
        await (enqueue or _enqueue_requeue)(command_id)
    return {
        "expired": expired,
        "lifted": lifted,
        "requeued": len(requeue),
        "outcome_unknown": unknown,
    }


async def _enqueue_requeue(command_id: int) -> None:
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    # A fresh job id: ARQ keeps a finished job's id for a while, and the
    # original id would be deduplicated away.
    await enqueue_job(
        FunctionNames.RUN_OPS_COMMAND,
        command_id,
        _job_id=f"ops-command-{command_id}-{int(datetime.now(UTC).timestamp())}",
    )


async def list_commands(
    session: AsyncSession, *, state: str | None = None, limit: int = 50
) -> list[CommandView]:
    query = select(OpsCommandModel).order_by(OpsCommandModel.created_at.desc())
    if state:
        query = query.where(OpsCommandModel.state == state)
    rows = (await session.scalars(query.limit(max(1, min(limit, 200))))).all()
    return [_view(row) for row in rows]


async def get(session: AsyncSession, command_id: int) -> CommandView:
    row = await session.get(OpsCommandModel, command_id)
    if row is None:
        raise LookupError(command_id)
    return _view(row)
