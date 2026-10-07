"""The typed support commands (handoff 33 "Requested action"; screen 33).

Each command is a fixed operation with typed parameters: what it changes is
previewed with old and new values, the state it was previewed against is
captured (so a run against a target that moved is refused, not applied),
and a run either succeeds, fails cleanly, or -- when something unexpected
happens midway -- is ``outcome_unknown`` until ``reconcile`` checks what
actually happened. There is no command that takes free text to execute:
no shell, no SQL, no AWS call. Adding one means adding a typed entry here.

Implemented: grant temporary usage, cancel a task, retry a task, pause a
routine, change one of a person's own preferences. Listed but not runnable,
each with the reason and what would make it possible: refund a payment,
export or delete customer data, repair a connection.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update

from api.db import db_client
from api.db.controls_models import QuotaAllowanceModel
from api.db.models import AgentRoutineModel, AgentTaskModel
from api.enums import StaffRole


class InvalidTarget(ValueError):
    """The command cannot be previewed against this target, as it is."""


class CommandFailed(Exception):
    """The command ran and was refused; nothing changed."""


@dataclass
class Context:
    organization_id: int
    target_user_id: int | None
    params: BaseModel
    ticket_requester_id: int | None = None


@dataclass
class RunContext:
    action_id: int
    organization_id: int
    target_user_id: int | None
    params: dict[str, Any]
    captured: dict[str, Any]
    reason: str
    requested_by: int
    approved_by: int | None
    run_by: int | None


@dataclass(frozen=True)
class Command:
    kind: str
    title: str
    description: str
    #: The lowest staff tier that may approve (never the requester).
    approver_role: str
    params_model: type[BaseModel] | None
    #: The customer's own request is required: a ticket from the target.
    needs_customer_request: bool = False
    #: Why it cannot run here at all (an unbuilt dependency), or None.
    unavailable_reason: str | None = None
    availability: Callable[[int], str | None] | None = None
    preview: Callable[[Context], Awaitable[dict[str, Any]]] | None = None
    run: Callable[[RunContext], Awaitable[dict[str, Any]]] | None = None
    reconcile: Callable[[RunContext], Awaitable[bool | None]] | None = None
    fields: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def state_for(self, organization_id: int) -> tuple[str, str | None]:
        """The capability state: available / needs_setup / unavailable."""
        if self.unavailable_reason:
            return "unavailable", self.unavailable_reason
        if self.availability is not None:
            missing = self.availability(organization_id)
            if missing:
                return "needs_setup", missing
        return "available", None


def version_of(
    kind: str,
    organization_id: int,
    target_user_id: int | None,
    params: dict[str, Any],
    captured: dict[str, Any],
) -> str:
    """The hash approval is bound to: what will be done, to whom, against
    which state. A label or a reason is not part of it."""
    body = json.dumps(
        {
            "kind": kind,
            "organization_id": organization_id,
            "target_user_id": target_user_id,
            "params": params,
            "captured": captured,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def marker(action_id: int) -> str:
    """Stamped on what a command creates, so reconciliation can find it."""
    return f"[support action #{action_id}]"


# --- grant temporary usage --------------------------------------------------


class GrantUsageParams(BaseModel):
    allowance: Literal[
        "model_turns", "voice_minutes", "outbound_messages", "browser_minutes"
    ]
    extra: int = Field(gt=0, le=10_000)
    days: int = Field(gt=0, le=31)

    model_config = ConfigDict(extra="forbid")


def _quotas_missing(_organization_id: int) -> str | None:
    from api.services import quotas

    if not quotas.enabled():
        return "Daily limits (operational_quotas) are switched off, so there is no limit to raise."
    return None


async def _grant_preview(ctx: Context) -> dict[str, Any]:
    from api.services import quotas

    p: GrantUsageParams = ctx.params  # type: ignore[assignment]
    if ctx.target_user_id is None:
        raise InvalidTarget("Choose the person to grant to.")
    usage = await quotas.usage(ctx.target_user_id, p.allowance)
    _one, many = quotas.UNITS[p.allowance]
    until = (datetime.now(UTC) + timedelta(days=p.days)).date().isoformat()
    return {
        "title": f"Add {p.extra} {many} a day for {p.days} days",
        "changes": [
            {"field": f"Daily {many}", "old": usage.limit, "new": usage.limit + p.extra}
        ],
        "impact": f"Up to {p.extra * p.days} more {many} in total, until {until}.",
        "dependencies": ["Daily limits are on (operational_quotas)."],
        "customer_summary": f"Extra {many}: {p.extra} more a day until {until}",
        "captured": {},
    }


async def _grant_run(ctx: RunContext) -> dict[str, Any]:
    from api.services import quotas

    p = GrantUsageParams(**ctx.params)
    try:
        row = await quotas.grant(
            user_id=int(ctx.target_user_id),
            kind=p.allowance,
            extra=p.extra,
            reason=f"{ctx.reason} {marker(ctx.action_id)}",
            expires_at=datetime.now(UTC) + timedelta(days=p.days),
            granted_by=ctx.requested_by,
        )
    except quotas.GrantRefused as exc:
        raise CommandFailed(str(exc)) from exc
    return {
        "summary": f"Granted {p.extra} more {quotas.UNITS[p.allowance][1]} a day.",
        "evidence": {"allowance_id": row.id, "expires_at": row.expires_at.isoformat()},
    }


async def _grant_reconcile(ctx: RunContext) -> bool | None:
    async with db_client.async_session() as session:
        found = await session.scalar(
            select(QuotaAllowanceModel.id).where(
                QuotaAllowanceModel.user_id == ctx.target_user_id,
                QuotaAllowanceModel.reason.like(f"%{marker(ctx.action_id)}%"),
            )
        )
    return found is not None


# --- cancel or retry a task -------------------------------------------------


class TaskParams(BaseModel):
    task_id: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


def _ledger_missing(organization_id: int) -> str | None:
    from api.services.workflow import task_ledger

    if not task_ledger.enabled(organization_id):
        return "The task ledger is off for this workspace, so task states cannot be changed safely."
    return None


async def _task_in_org(ctx: Context) -> Any:
    p: TaskParams = ctx.params  # type: ignore[assignment]
    task = await db_client.get_task(p.task_id, organization_id=ctx.organization_id)
    if task is None:
        raise InvalidTarget("That task is not in this workspace.")
    return task


async def _cancel_preview(ctx: Context) -> dict[str, Any]:
    from api.services.workflow import task_ledger

    task = await _task_in_org(ctx)
    state = task_ledger.state_of(task)
    if state in task_ledger.TERMINAL:
        raise InvalidTarget(f"The task is already {state}.")
    if state == task_ledger.OUTCOME_UNKNOWN:
        raise InvalidTarget("Its outcome is unknown: reconcile it before cancelling.")
    return {
        "title": f"Cancel task #{task.id}",
        "changes": [
            {"field": "Task state", "old": state, "new": task_ledger.CANCELLED}
        ],
        "impact": "The task stops; nothing it already did is undone.",
        "dependencies": [
            f"The task is still at version {int(task.state_version or 0)}."
        ],
        "customer_summary": f"Cancel task #{task.id}",
        "captured": {"state": state, "version": int(task.state_version or 0)},
    }


async def _cancel_run(ctx: RunContext) -> dict[str, Any]:
    from api.services.workflow import task_ledger

    try:
        task = await task_ledger.transition(
            organization_id=ctx.organization_id,
            task_id=int(ctx.params["task_id"]),
            to_state=task_ledger.CANCELLED,
            expected_version=int(ctx.captured["version"]),
            actor_user_id=ctx.run_by,
            reason_code="support_cancel",
        )
    except task_ledger.StaleState as exc:
        raise CommandFailed(
            "The task changed since the preview; nothing was cancelled."
        ) from exc
    except task_ledger.LedgerError as exc:
        raise CommandFailed(str(exc)) from exc
    return {
        "summary": f"Task #{task.id} cancelled.",
        "evidence": {"task_id": task.id, "version": int(task.state_version or 0)},
    }


async def _cancel_reconcile(ctx: RunContext) -> bool | None:
    from api.services.workflow import task_ledger

    task = await db_client.get_task(
        int(ctx.params["task_id"]), organization_id=ctx.organization_id
    )
    return task is not None and task_ledger.state_of(task) == task_ledger.CANCELLED


def _retry_key(action_id: int, task_id: int) -> str:
    return f"support-retry:{task_id}:{action_id}"


async def _retry_preview(ctx: Context) -> dict[str, Any]:
    from api.services.workflow import task_ledger

    task = await _task_in_org(ctx)
    state = task_ledger.state_of(task)
    if state == task_ledger.OUTCOME_UNKNOWN:
        raise InvalidTarget(
            "Its outcome is unknown: check whether it already happened before retrying."
        )
    if state == task_ledger.COMPLETED:
        raise InvalidTarget("The task completed; there is nothing to retry.")
    if state not in (task_ledger.FAILED, task_ledger.CANCELLED):
        raise InvalidTarget(
            f"The task is {state.replace('_', ' ')}; only a failed or cancelled task is retried."
        )
    helper = (
        f"agent #{task.assignee_workflow_id}"
        if task.assignee_workflow_id
        else "no agent (it waits on the board)"
    )
    return {
        "title": f"Retry task #{task.id}",
        "changes": [
            {
                "field": f"Task #{task.id}",
                "old": state,
                "new": f"{state} (kept as it is)",
            },
            {
                "field": "New task",
                "old": "none",
                "new": f"a queued copy, given to {helper}",
            },
        ],
        "impact": "Runs the task again from the start; anything it sends still asks the customer first.",
        "dependencies": [f"The task is still {state}."],
        "customer_summary": f"Try task #{task.id} again",
        "captured": {"state": state, "version": int(task.state_version or 0)},
    }


async def _retry_run(ctx: RunContext) -> dict[str, Any]:
    from api.services.workflow import task_ledger

    original = await db_client.get_task(
        int(ctx.params["task_id"]), organization_id=ctx.organization_id
    )
    if original is None:
        raise CommandFailed("The task is gone.")
    if int(original.state_version or 0) != int(ctx.captured["version"]):
        raise CommandFailed("The task changed since the preview; nothing was retried.")
    task, created = await task_ledger.create(
        organization_id=ctx.organization_id,
        title=original.title,
        brief=original.brief or "",
        created_by=original.created_by,
        idempotency_key=_retry_key(ctx.action_id, original.id),
        assignee_workflow_id=original.assignee_workflow_id,
        assignee_user_id=original.assignee_user_id,
    )
    handed = False
    if created and original.assignee_workflow_id:
        from api.tasks.arq import enqueue_job
        from api.tasks.function_names import FunctionNames

        try:
            await enqueue_job(FunctionNames.RUN_AGENT_TASK, task.id)
            handed = True
        except Exception as exc:  # noqa: BLE001 - the copy exists; say it waits
            logger.error("Support retry {}: could not wake the agent: {}", task.id, exc)
    return {
        "summary": (
            f"Queued again as task #{task.id}"
            + (", handed to its agent." if handed else "; it waits on the board.")
        ),
        "evidence": {"new_task_id": task.id, "handed_to_agent": handed},
    }


async def _retry_reconcile(ctx: RunContext) -> bool | None:
    async with db_client.async_session() as session:
        found = await session.scalar(
            select(AgentTaskModel.id).where(
                AgentTaskModel.organization_id == ctx.organization_id,
                AgentTaskModel.idempotency_key
                == _retry_key(ctx.action_id, int(ctx.params["task_id"])),
            )
        )
    return found is not None


# --- pause a routine --------------------------------------------------------


class RoutineParams(BaseModel):
    routine_id: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


async def _pause_preview(ctx: Context) -> dict[str, Any]:
    p: RoutineParams = ctx.params  # type: ignore[assignment]
    routine = await db_client.get_routine(
        p.routine_id, organization_id=ctx.organization_id
    )
    if routine is None:
        raise InvalidTarget("That routine is not in this workspace.")
    if not routine.is_active:
        raise InvalidTarget("The routine is already paused.")
    return {
        "title": f"Pause the routine “{routine.name}”",
        "changes": [{"field": f"Routine #{routine.id}", "old": "on", "new": "paused"}],
        "impact": "It stops running on its schedule until the customer turns it back on.",
        "dependencies": ["The routine is still on."],
        "customer_summary": f"Pause the routine “{routine.name}”",
        "captured": {"is_active": True},
    }


async def _pause_run(ctx: RunContext) -> dict[str, Any]:
    async with db_client.async_session() as session:
        hit = (
            await session.execute(
                update(AgentRoutineModel)
                .where(
                    AgentRoutineModel.id == int(ctx.params["routine_id"]),
                    AgentRoutineModel.organization_id == ctx.organization_id,
                    AgentRoutineModel.is_active.is_(True),
                )
                .values(is_active=False)
                .returning(AgentRoutineModel.id)
            )
        ).first()
        if hit is None:
            await session.rollback()
            raise CommandFailed("The routine was already paused or is gone.")
        await session.commit()
    return {
        "summary": f"Routine #{hit[0]} paused.",
        "evidence": {"routine_id": hit[0]},
    }


async def _pause_reconcile(ctx: RunContext) -> bool | None:
    routine = await db_client.get_routine(
        int(ctx.params["routine_id"]), organization_id=ctx.organization_id
    )
    return routine is not None and not routine.is_active


# --- change one of a person's own preferences --------------------------------


class PreferenceParams(BaseModel):
    field: Literal["language", "timezone", "summary_time"]
    value: str = Field(min_length=1, max_length=64)

    model_config = ConfigDict(extra="forbid")


def _preferences_missing(_organization_id: int) -> str | None:
    from api.services import member_preferences

    if not member_preferences.enabled():
        return "Personal preferences (member_preferences) are switched off."
    return None


async def _preference_preview(ctx: Context) -> dict[str, Any]:
    from api.services import member_preferences

    p: PreferenceParams = ctx.params  # type: ignore[assignment]
    if ctx.target_user_id is None:
        raise InvalidTarget("Choose whose preference changes.")
    try:
        clean = member_preferences.validate({p.field: p.value})
    except member_preferences.PreferenceInvalid as exc:
        raise InvalidTarget(str(exc)) from exc
    current = await member_preferences.get(ctx.target_user_id)
    old = current.get(p.field)
    if old == clean[p.field]:
        raise InvalidTarget("That is already the value.")
    label = p.field.replace("_", " ")
    return {
        "title": f"Change their {label}",
        "changes": [
            {
                "field": label.capitalize(),
                "old": old or "not set",
                "new": clean[p.field],
            }
        ],
        "impact": "Only this person's own setting; nobody else's and no workspace default.",
        "dependencies": [
            f"Their preferences are still at revision {current['revision']}."
        ],
        "customer_summary": f"Change your {label} to {clean[p.field]}",
        "captured": {"revision": int(current["revision"])},
    }


async def _preference_run(ctx: RunContext) -> dict[str, Any]:
    from api.services import member_preferences

    p = PreferenceParams(**ctx.params)
    try:
        saved = await member_preferences.save(
            int(ctx.target_user_id),
            {p.field: p.value},
            revision=int(ctx.captured["revision"]),
        )
    except member_preferences.Conflict as exc:
        raise CommandFailed(
            "Their preferences changed since the preview; nothing was saved."
        ) from exc
    except member_preferences.PreferenceInvalid as exc:
        raise CommandFailed(str(exc)) from exc
    return {
        "summary": f"{p.field.replace('_', ' ').capitalize()} set to {saved[p.field]}.",
        "evidence": {"revision": saved["revision"]},
    }


async def _preference_reconcile(ctx: RunContext) -> bool | None:
    from api.services import member_preferences

    p = PreferenceParams(**ctx.params)
    current = await member_preferences.get(int(ctx.target_user_id))
    return current.get(p.field) == p.value


# --- the registry -----------------------------------------------------------

SUPPORT = StaffRole.SUPPORT.value
SUPERADMIN = StaffRole.SUPERADMIN.value

COMMANDS: dict[str, Command] = {
    "grant_usage": Command(
        kind="grant_usage",
        title="Grant temporary usage",
        description="Raise one daily allowance for one person, for a few days.",
        approver_role=SUPERADMIN,
        params_model=GrantUsageParams,
        availability=_quotas_missing,
        preview=_grant_preview,
        run=_grant_run,
        reconcile=_grant_reconcile,
        fields=(
            {
                "name": "allowance",
                "label": "Allowance",
                "type": "choice",
                "choices": [
                    "model_turns",
                    "voice_minutes",
                    "outbound_messages",
                    "browser_minutes",
                ],
            },
            {
                "name": "extra",
                "label": "Extra per day",
                "type": "integer",
                "min": 1,
                "max": 10000,
            },
            {
                "name": "days",
                "label": "For how many days",
                "type": "integer",
                "min": 1,
                "max": 31,
            },
        ),
    ),
    "cancel_task": Command(
        kind="cancel_task",
        title="Cancel a task",
        description="Stop a task that has not finished.",
        approver_role=SUPPORT,
        params_model=TaskParams,
        needs_customer_request=True,
        availability=_ledger_missing,
        preview=_cancel_preview,
        run=_cancel_run,
        reconcile=_cancel_reconcile,
        fields=({"name": "task_id", "label": "Task id", "type": "integer", "min": 1},),
    ),
    "retry_task": Command(
        kind="retry_task",
        title="Retry a task",
        description="Run a failed or cancelled task again as a new task.",
        approver_role=SUPPORT,
        params_model=TaskParams,
        needs_customer_request=True,
        availability=_ledger_missing,
        preview=_retry_preview,
        run=_retry_run,
        reconcile=_retry_reconcile,
        fields=({"name": "task_id", "label": "Task id", "type": "integer", "min": 1},),
    ),
    "pause_routine": Command(
        kind="pause_routine",
        title="Pause a routine",
        description="Stop a routine running on its schedule.",
        approver_role=SUPPORT,
        params_model=RoutineParams,
        needs_customer_request=True,
        preview=_pause_preview,
        run=_pause_run,
        reconcile=_pause_reconcile,
        fields=(
            {"name": "routine_id", "label": "Routine id", "type": "integer", "min": 1},
        ),
    ),
    "change_preference": Command(
        kind="change_preference",
        title="Change a personal setting",
        description="Language, timezone or summary time -- the person's own, as they asked.",
        approver_role=SUPPORT,
        params_model=PreferenceParams,
        needs_customer_request=True,
        availability=_preferences_missing,
        preview=_preference_preview,
        run=_preference_run,
        reconcile=_preference_reconcile,
        fields=(
            {
                "name": "field",
                "label": "Setting",
                "type": "choice",
                "choices": ["language", "timezone", "summary_time"],
            },
            {"name": "value", "label": "New value", "type": "text"},
        ),
    ),
    "refund_payment": Command(
        kind="refund_payment",
        title="Refund a payment",
        description="Finance only: a payment reference, an eligible amount, reconciled with the provider.",
        approver_role=SUPERADMIN,
        params_model=None,
        unavailable_reason=(
            "Needs a finance role and the ledger and refund screen (stream `staff`, "
            "screen 38). Decibyl is free while early, so there is nothing to refund yet."
        ),
    ),
    "export_data": Command(
        kind="export_data",
        title="Export customer data",
        description="Verify identity and scope, then export across stores and processors.",
        approver_role=SUPERADMIN,
        params_model=None,
        unavailable_reason=(
            "The customer exports their own data from Privacy, where their identity "
            "is checked. A staff-run export needs identity verification across every "
            "store and processor, which is not built."
        ),
    ),
    "delete_data": Command(
        kind="delete_data",
        title="Delete customer data",
        description="Verify identity and scope, then delete across stores and processors.",
        approver_role=SUPERADMIN,
        params_model=None,
        unavailable_reason=(
            "The customer deletes their data or closes their workspace from Privacy. "
            "A staff-run deletion needs identity verification and per-store progress, "
            "which is not built."
        ),
    ),
    "repair_connection": Command(
        kind="repair_connection",
        title="Repair a connection",
        description="Diagnose; the customer reconnects.",
        approver_role=SUPPORT,
        params_model=None,
        unavailable_reason=(
            "Staff cannot reconnect an app for a customer or ask for their password: "
            "the customer completes the app's own sign-in. Reply on the ticket with "
            "what to reconnect."
        ),
    ),
}


def get(kind: str) -> Command:
    command = COMMANDS.get(kind)
    if command is None:
        raise InvalidTarget("That is not a support command.")
    return command


def parse(command: Command, params: dict[str, Any]) -> BaseModel:
    if command.params_model is None:
        raise InvalidTarget(
            command.unavailable_reason or "This command takes no parameters."
        )
    try:
        return command.params_model(**(params or {}))
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise InvalidTarget(f"{where}: {first.get('msg')}") from exc


def catalogue(organization_id: int | None) -> list[dict[str, Any]]:
    """Every command, with its state for one workspace (or in general)."""
    out = []
    for command in COMMANDS.values():
        if organization_id is None:
            state, reason = (
                ("unavailable", command.unavailable_reason)
                if command.unavailable_reason
                else ("available", None)
            )
        else:
            state, reason = command.state_for(organization_id)
        out.append(
            {
                "kind": command.kind,
                "title": command.title,
                "description": command.description,
                "approver_role": command.approver_role,
                "needs_customer_request": command.needs_customer_request,
                "state": state,
                "reason": reason,
                "fields": list(command.fields),
            }
        )
    return out
