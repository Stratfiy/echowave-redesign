"""The staff console (launch stream `staff`; handoff 32, 36, 37; screens
29-31, 34-44).

Every route is a 404 while ``staff_console`` is off, staff-only, and asks
``roles.require`` for the capability its screen needs, so the console's
role boundaries hold however a request is made. Mounted under ``/admin``
with an ``admin-`` tag, so it stays out of the public OpenAPI document.

Thin by design: parse, authorise, delegate to ``services/staff``, shape.
The ops stream's services (health, provider keys, typed infrastructure
commands, Laya, cost stop) live at ``/admin/ops`` and are called by the
console directly, not wrapped here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api.db import admin_audit_client, db_client
from api.services import features
from api.services.auth.depends import get_staff
from api.services.staff import (
    analytics,
    commands,
    evaluations,
    incidents,
    operations,
    overview,
    policy,
    refunds,
    revenue,
    role_admin,
    roles,
    users,
    workspaces,  # noqa: F401  (registers workspace.suspend / unsuspend)
)

router = APIRouter(
    prefix="/admin/staff",
    tags=["admin-staff"],
    dependencies=[Depends(features.require("staff_console")), Depends(get_staff)],
)

Ctx = roles.StaffContext


def _days(default: int = 28) -> Any:
    return Query(default=default, ge=1, le=365)


@router.get("/me")
async def me(
    ctx: Annotated[Ctx, Depends(roles.require("overview.read"))],
) -> dict[str, Any]:
    """Who the console is talking to: roles, capabilities and destinations.
    The UI draws from this; the backend still checks every request."""
    from api import constants

    return {
        "user_id": ctx.user.id,
        "email": ctx.user.email,
        "tier": ctx.user.staff_role,
        "mfa_enabled": bool(ctx.user.mfa_enabled),
        "roles": sorted(ctx.roles),
        "capabilities": roles.capabilities_for(ctx.roles),
        "destinations": roles.destinations_for(ctx.roles),
        "environment": constants.ENVIRONMENT,
        "features": {
            name: features.is_on(name)
            for name in (
                "staff_roles",
                "staff_refunds",
                "staff_evaluations",
                "staff_incidents",
                "operational_quotas",
                "event_catalogue",
                "reply_feedback",
                "free_mode",
            )
        },
    }


# --- overview ------------------------------------------------------------------


@router.get("/overview")
async def founder_overview(
    _: Annotated[Ctx, Depends(roles.require("overview.read"))],
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return await overview.snapshot(session)


# --- users and access ----------------------------------------------------------


@router.get("/users")
async def list_users(
    _: Annotated[Ctx, Depends(roles.require("users.read"))],
    q: str | None = Query(default=None, max_length=120),
    state: str | None = Query(default=None, pattern="^(active|suspended)$"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return await users.list_users(session, query=q, state=state, limit=limit)


@router.get("/waitlist")
async def waitlist(
    _: Annotated[Ctx, Depends(roles.require("users.read"))],
    status: str | None = Query(default="waitlisted", max_length=16),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return {
            "requests": await users.waitlist(session, status=status),
            "capacity": await users.capacity(session),
        }


@router.get("/users/{user_id}")
async def user_detail(
    user_id: int, _: Annotated[Ctx, Depends(roles.require("users.read"))]
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        found = await users.detail(session, user_id)
        if found is None:
            raise HTTPException(status_code=404, detail="No such user.")
        found["connections"] = await users.connections(session, user_id)
    found["limits"] = await users.limits(user_id)
    return found


@router.post("/users/{user_id}/assisted-access/end")
async def end_assisted_access(
    user_id: int,
    request: Request,
    ctx: Annotated[Ctx, Depends(roles.require("users.suspend.request"))],
) -> dict[str, Any]:
    """End an open view-as or impersonation of this person. The borrowed
    session is refused on its next request (it is revoked on the server, not
    only in the browser that holds it)."""
    async with db_client.async_session() as session:
        return await users.end_assisted_access(
            session,
            user_id,
            actor_user_id=ctx.user.id,
            actor_ip=request.client.host if request.client else None,
        )


@router.get("/users/{user_id}/tasks")
async def user_tasks(
    user_id: int,
    _: Annotated[Ctx, Depends(roles.require("users.read"))],
    organization_id: int | None = Query(default=None),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        found = await users.detail(session, user_id)
        if found is None:
            raise HTTPException(status_code=404, detail="No such user.")
        if organization_id is not None and organization_id not in {
            w["id"] for w in found["workspaces"]
        }:
            # Another workspace's tasks are not this person's to show.
            raise HTTPException(
                status_code=404, detail="This person is not in that workspace."
            )
        return await users.tasks(session, user_id, organization_id)


# --- commands --------------------------------------------------------------------


@router.get("/commands/catalogue")
async def command_catalogue(
    ctx: Annotated[Ctx, Depends(roles.require("overview.read"))],
) -> dict[str, Any]:
    return {"commands": commands.catalogue(ctx.roles)}


@router.get("/commands")
async def list_commands(
    _: Annotated[Ctx, Depends(roles.require("overview.read"))],
    state: str | None = Query(default=None, max_length=24),
    prefix: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        rows = await commands.list_commands(
            session, state=state, command_prefix=prefix, limit=limit
        )
    return {"commands": [r.as_dict() for r in rows]}


class CommandRequest(BaseModel):
    command: str = Field(max_length=48)
    target: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=4, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)
    environment: str | None = Field(default=None, max_length=32)


def _command_error(exc: commands.CommandError) -> HTTPException:
    if isinstance(exc, commands.NotPermitted):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, commands.Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, commands.NotSwitchedOn):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


class PreviewRequest(BaseModel):
    command: str = Field(max_length=48)
    target: dict[str, Any] = Field(default_factory=dict)


@router.post("/commands/preview")
async def preview_command(
    body: PreviewRequest, ctx: Annotated[Ctx, Depends(roles.require("overview.read"))]
) -> dict[str, Any]:
    """The exact effect of a command before anyone asks for it. Writes
    nothing; eligibility is checked again when it is requested."""
    async with db_client.async_session() as session:
        try:
            return await commands.dry_run(
                session, ctx=ctx, command=body.command, target=body.target
            )
        except commands.CommandError as exc:
            raise _command_error(exc) from None


@router.post("/commands", status_code=202)
async def request_command(
    body: CommandRequest, ctx: Annotated[Ctx, Depends(roles.require("overview.read"))]
) -> dict[str, Any]:
    """Accepted is not succeeded: read ``state``. ``queued`` commands run in
    the worker; poll ``GET /commands/{id}``."""
    async with db_client.async_session() as session:
        try:
            view = await commands.request(
                session,
                ctx=ctx,
                command=body.command,
                target=body.target,
                reason=body.reason,
                idempotency_key=body.idempotency_key,
                environment=body.environment,
            )
        except commands.CommandError as exc:
            raise _command_error(exc) from None
        await session.commit()
    if commands.needs_worker(view):
        await commands.enqueue(view.id)
    return view.as_dict()


@router.get("/commands/{command_id}")
async def get_command(
    command_id: int, _: Annotated[Ctx, Depends(roles.require("overview.read"))]
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            return (await commands.get(session, command_id)).as_dict()
        except LookupError:
            raise HTTPException(status_code=404, detail="No such command.") from None


@router.post("/commands/{command_id}/approve")
async def approve_command(
    command_id: int, ctx: Annotated[Ctx, Depends(roles.require("overview.read"))]
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            view = await commands.approve(session, ctx=ctx, command_id=command_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="No such command.") from None
        except commands.CommandError as exc:
            await session.commit()  # an expiry found here is recorded
            raise _command_error(exc) from None
        await session.commit()
    if commands.needs_worker(view):
        await commands.enqueue(view.id)
    return view.as_dict()


class RejectRequest(BaseModel):
    note: str = Field(default="", max_length=300)


@router.post("/commands/{command_id}/reject")
async def reject_command(
    command_id: int,
    body: RejectRequest,
    ctx: Annotated[Ctx, Depends(roles.require("overview.read"))],
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            view = await commands.reject(
                session, ctx=ctx, command_id=command_id, note=body.note
            )
        except LookupError:
            raise HTTPException(status_code=404, detail="No such command.") from None
        except commands.CommandError as exc:
            raise _command_error(exc) from None
        await session.commit()
    return view.as_dict()


# --- quality and evaluations ----------------------------------------------------

_evals = Depends(features.require("staff_evaluations"))


@router.get("/quality/datasets", dependencies=[_evals])
async def eval_datasets(
    _: Annotated[Ctx, Depends(roles.require("quality.read"))],
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return {
            "datasets": await evaluations.datasets(session),
            "min_sample": evaluations.MIN_SAMPLE,
            "runners": list(evaluations.RUNNERS),
        }


@router.get("/quality/datasets/{dataset}/cases", dependencies=[_evals])
async def eval_cases(
    dataset: str, _: Annotated[Ctx, Depends(roles.require("quality.read"))]
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return {"cases": await evaluations.list_cases(session, dataset)}


@router.get("/quality/runs", dependencies=[_evals])
async def eval_runs(
    _: Annotated[Ctx, Depends(roles.require("quality.read"))],
    dataset: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return {"runs": await evaluations.list_runs(session, dataset)}


@router.get("/quality/runs/{run_id}", dependencies=[_evals])
async def eval_run(
    run_id: int, _: Annotated[Ctx, Depends(roles.require("quality.read"))]
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        found = await evaluations.run_detail(session, run_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such run.")
    return found


@router.get("/quality/runs/{run_id}/cases/{case_id}", dependencies=[_evals])
async def eval_compare(
    run_id: int, case_id: int, _: Annotated[Ctx, Depends(roles.require("quality.read"))]
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        found = await evaluations.comparison(session, run_id, case_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such run or case.")
    return found


# --- analytics --------------------------------------------------------------------


@router.get("/analytics")
async def product_analytics(
    _: Annotated[Ctx, Depends(roles.require("analytics.read"))], days: int = _days()
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return await analytics.report(session, days=days)


@router.get("/analytics/records")
async def analytics_records(
    _: Annotated[Ctx, Depends(roles.require("analytics.read"))], days: int = _days()
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return await analytics.records(session, days=days)


# --- revenue, ledger and refunds ---------------------------------------------------


@router.get("/revenue")
async def revenue_report(
    _: Annotated[Ctx, Depends(roles.require("revenue.read"))], days: int = _days(30)
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return await revenue.report(session, days=days)


def _ledger_filters(
    status: str | None = Query(default=None, pattern="^(created|paid|failed)$"),
    organization_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    return {"status": status, "organization_id": organization_id, "limit": limit}


@router.get("/ledger")
async def ledger(
    _: Annotated[Ctx, Depends(roles.require("revenue.read"))],
    filters: Annotated[dict, Depends(_ledger_filters)],
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return await refunds.ledger(session, **filters)


@router.get("/ledger/export.csv", response_class=PlainTextResponse)
async def ledger_export(
    _: Annotated[Ctx, Depends(roles.require("revenue.read"))],
    filters: Annotated[dict, Depends(_ledger_filters)],
) -> PlainTextResponse:
    async with db_client.async_session() as session:
        body = await refunds.export_csv(session, **filters)
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="decibyl-ledger.csv"'},
    )


@router.get("/ledger/{payment_id}")
async def ledger_transaction(
    payment_id: int, _: Annotated[Ctx, Depends(roles.require("revenue.read"))]
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        found = await refunds.transaction(session, payment_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such payment.")
    found["refunds_enabled"] = features.is_on("staff_refunds")
    return found


# --- operations -----------------------------------------------------------------------


@router.get("/operations")
async def operations_summary(
    _: Annotated[Ctx, Depends(roles.require("operations.read"))],
    hours: int = Query(default=24, ge=1, le=168),
) -> dict[str, Any]:
    from api.services import system_status

    async with db_client.async_session() as session:
        jobs = await operations.jobs(session, hours=hours)
        delivery = await operations.delivery(session, hours=hours)
        calls = await operations.active_calls(session)
    try:
        snapshot = await system_status.snapshot()
        infra = operations.health_from_probes(snapshot)
        providers = (snapshot.get("provider_balances") or {}).get("providers", [])
    except Exception as exc:  # noqa: BLE001 -- unknown, not healthy
        infra = {"state": "unknown", "signals": [], "reason": type(exc).__name__}
        providers = []
    return {
        "jobs": jobs,
        "delivery": delivery,
        "calls": calls,
        "infrastructure": infra,
        "providers": providers,
    }


@router.get("/operations/trace/{task_id}")
async def task_trace(
    task_id: int, _: Annotated[Ctx, Depends(roles.require("operations.read"))]
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        found = await operations.trace(session, task_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such task.")
    return found


@router.get("/operations/latency")
async def voice_latency(
    _: Annotated[Ctx, Depends(roles.require("operations.read"))],
    days: int = _days(7),
    language: str | None = Query(default=None, max_length=16),
    organization_id: int | None = Query(default=None),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return await operations.voice_latency(
            session, days=days, language=language, organization_id=organization_id
        )


_incidents = Depends(features.require("staff_incidents"))


@router.get("/incidents", dependencies=[_incidents])
async def list_incidents(
    _: Annotated[Ctx, Depends(roles.require("operations.read"))],
    open_only: bool = False,
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return {
            "incidents": await incidents.list_incidents(session, open_only=open_only)
        }


@router.get("/incidents/{incident_id}", dependencies=[_incidents])
async def incident(
    incident_id: int, _: Annotated[Ctx, Depends(roles.require("operations.read"))]
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        found = await incidents.get(session, incident_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such incident.")
    return found


# --- controls and audit ------------------------------------------------------------------


@router.get("/policy")
async def policy_report(
    _: Annotated[Ctx, Depends(roles.require("policy.read"))],
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return await policy.report(session)


@router.get("/roles")
async def staff_roles(
    _: Annotated[Ctx, Depends(roles.require("roles.manage"))],
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        members = await role_admin.staff_members(session)
    return {
        "matrix": roles.matrix(),
        "members": members,
        "grants_enabled": features.is_on("staff_roles"),
    }


@router.get("/audit")
async def staff_audit(
    _: Annotated[Ctx, Depends(roles.require("audit.read"))],
    actor_user_id: int | None = Query(default=None),
    action: str | None = Query(default=None, max_length=48),
    before: str | None = Query(default=None),
    limit: int = Query(
        default=admin_audit_client.DEFAULT_LIMIT, ge=1, le=admin_audit_client.MAX_LIMIT
    ),
) -> dict[str, Any]:
    """The same audit stream as ``/admin/audit``, behind the console's own
    capability so it follows the role matrix."""
    try:
        cursor = admin_audit_client.parse_cursor(before)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed `before`") from exc
    async with db_client.async_session() as session:
        page = await admin_audit_client.audit_page(
            session,
            organization_id=None,
            actor_user_id=actor_user_id,
            action=action or None,
            before=cursor,
            limit=limit,
        )
        page["actions"] = await admin_audit_client.known_actions(session)
    return page
