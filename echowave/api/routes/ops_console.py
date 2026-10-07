"""Backing services for the staff console's operations screens (stream ops).

Screens 39 (operations and delivery), 41 (incident and runbook), 42
(providers and secret lifecycle), 43 (flags, budgets and model policy) and
the Laya evaluation report call these; the ``staff`` stream builds the
screens. Every route is a 404 while ``ops_console`` is off, staff-only
(support or above; key lifecycle is superadmin), and out of the public
OpenAPI (``/admin/`` path, ``admin-`` tag).

Thin by design: parse, authorise, delegate to ``services/ops``, shape. No
route ever returns a secret value, and request bodies that carry one are
excluded from logs and error reports (``sentry_scrub`` drops bodies; nothing
here logs a body).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_staff, get_superuser
from api.services.configuration.platform_credentials import PlatformCredentialError
from api.services.ops import (
    commands,
    cost_stop,
    credentials,
    evidence,
    infra_health,
    laya_eval,
)

router = APIRouter(
    prefix="/admin/ops",
    tags=["admin-ops"],
    dependencies=[Depends(features.require("ops_console")), Depends(get_staff)],
)


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# --- health and evidence ----------------------------------------------------


@router.get("/health")
async def ops_health() -> dict[str, Any]:
    """Workers, queues, Redis, the Postgres pool and the freshness of every
    piece of evidence. ``ok`` only when every core signal is measured ok."""
    return await infra_health.snapshot()


@router.get("/evidence")
async def ops_evidence(
    kind: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200)
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        latest = await evidence.summary(session)
        rows = await evidence.recent(session, kind=kind, limit=limit)
    return {"latest": latest, "recent": [row.as_dict() for row in rows]}


class EvidenceRequest(BaseModel):
    kind: str
    outcome: str
    summary: str = Field(min_length=1, max_length=500)
    metrics: dict[str, Any] = Field(default_factory=dict)
    link: str | None = Field(default=None, max_length=500)


@router.post("/evidence")
async def record_evidence(
    body: EvidenceRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    """Record a drill or review run elsewhere (CI, a terminal)."""
    async with db_client.async_session() as session:
        try:
            row = await evidence.record(
                session,
                kind=body.kind,
                outcome=body.outcome,
                summary=body.summary,
                metrics=body.metrics,
                link=body.link,
                recorded_by=user.id,
            )
        except evidence.EvidenceError as exc:
            raise _bad(exc) from None
        await session.commit()
    return row.as_dict()


# --- provider keys and their lifecycle (superadmin) -------------------------


@router.get("/credentials")
async def provider_keys(_user: UserModel = Depends(get_superuser)) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return {
            "keys": await credentials.provider_keys(session),
            "rotations": [
                r.as_dict() for r in await credentials.list_rotations(session)
            ],
        }


class StageRequest(BaseModel):
    component: str
    provider: str
    #: Write-only. Never echoed, logged or stored in plaintext.
    api_key: str = Field(min_length=8, max_length=4096)
    reason: str = Field(min_length=4, max_length=500)
    label: str | None = Field(default=None, max_length=128)


class StepRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    force: bool = False


@router.post("/credentials/rotations")
async def stage_rotation(
    body: StageRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            view = await credentials.stage(
                session,
                actor_user_id=user.id,
                component=body.component,
                provider=body.provider,
                api_key=body.api_key,
                reason=body.reason,
                label=body.label,
            )
        except (credentials.LifecycleError, PlatformCredentialError) as exc:
            raise _bad(exc) from None
        await session.commit()
    return view.as_dict()


_STEPS = {
    "validate": lambda s, rid, uid, body: credentials.validate(
        s, rotation_id=rid, actor_user_id=uid
    ),
    "activate": lambda s, rid, uid, body: credentials.activate(
        s, rotation_id=rid, actor_user_id=uid, force=body.force
    ),
    "refresh": lambda s, rid, uid, body: credentials.refresh_consumers(
        s, rotation_id=rid, actor_user_id=uid
    ),
    "verify": lambda s, rid, uid, body: credentials.verify(
        s, rotation_id=rid, actor_user_id=uid
    ),
    "revoke": lambda s, rid, uid, body: credentials.revoke(
        s, rotation_id=rid, actor_user_id=uid, reason=body.reason or ""
    ),
    "revert": lambda s, rid, uid, body: credentials.revert(
        s, rotation_id=rid, actor_user_id=uid, reason=body.reason or ""
    ),
    "cancel": lambda s, rid, uid, body: credentials.cancel(
        s, rotation_id=rid, actor_user_id=uid, reason=body.reason or ""
    ),
}


@router.post("/credentials/rotations/{rotation_id}/{step}")
async def rotation_step(
    rotation_id: int,
    step: str,
    body: StepRequest | None = None,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    run = _STEPS.get(step)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown step {step}")
    async with db_client.async_session() as session:
        try:
            view = await run(session, rotation_id, user.id, body or StepRequest())
        except LookupError:
            raise HTTPException(status_code=404, detail="No such rotation") from None
        except credentials.StepNotAllowed as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except (credentials.LifecycleError, PlatformCredentialError) as exc:
            # The step may have recorded why it failed; keep that.
            await session.commit()
            raise _bad(exc) from None
        await session.commit()
    return view.as_dict()


# --- typed commands ---------------------------------------------------------


@router.get("/commands/catalogue")
async def command_catalogue(user: UserModel = Depends(get_staff)) -> dict[str, Any]:
    return {"commands": commands.catalogue(user)}


@router.get("/commands")
async def list_commands(
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        rows = await commands.list_commands(session, state=state, limit=limit)
    return {"commands": [row.as_dict() for row in rows]}


class CommandRequest(BaseModel):
    command: str
    target: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=4, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)
    environment: str | None = None


def _command_error(exc: commands.CommandError) -> HTTPException:
    if isinstance(exc, commands.NotPermitted):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, commands.Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    return _bad(exc)


@router.post("/commands", status_code=202)
async def request_command(
    body: CommandRequest, user: UserModel = Depends(get_staff)
) -> dict[str, Any]:
    """Accepted is queued, not succeeded: read ``state`` on the response and
    poll ``GET /commands/{id}`` for the result."""
    async with db_client.async_session() as session:
        try:
            view = await commands.request(
                session,
                user=user,
                command=body.command,
                target=body.target,
                reason=body.reason,
                idempotency_key=body.idempotency_key,
                environment=body.environment,
            )
        except commands.CommandError as exc:
            raise _command_error(exc) from None
        await session.commit()
    return view.as_dict()


@router.get("/commands/{command_id}")
async def get_command(command_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            view = await commands.refresh(session, command_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="No such command") from None
        except Exception:  # noqa: BLE001 - SSM unreadable: show what we have
            view = await commands.get(session, command_id)
        await session.commit()
    return view.as_dict()


@router.post("/commands/{command_id}/approve")
async def approve_command(
    command_id: int, user: UserModel = Depends(get_staff)
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            view = await commands.approve(session, user=user, command_id=command_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="No such command") from None
        except commands.CommandError as exc:
            await session.commit()
            raise _command_error(exc) from None
        await session.commit()
    return view.as_dict()


class RejectRequest(BaseModel):
    note: str = Field(default="", max_length=300)


@router.post("/commands/{command_id}/reject")
async def reject_command(
    command_id: int, body: RejectRequest, user: UserModel = Depends(get_staff)
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            view = await commands.reject(
                session, user=user, command_id=command_id, note=body.note
            )
        except LookupError:
            raise HTTPException(status_code=404, detail="No such command") from None
        except commands.CommandError as exc:
            raise _command_error(exc) from None
        await session.commit()
    return view.as_dict()


# --- Laya and cost stop -----------------------------------------------------


@router.get("/laya")
async def laya_report(days: int = Query(default=7, ge=1, le=35)) -> dict[str, Any]:
    """Live shadow agreement (counts only), breaker and rollback state, and
    the newest offline evaluation on record."""
    async with db_client.async_session() as session:
        latest = await evidence.latest(session, "laya_evaluation")
    return {
        "shadow": await laya_eval.shadow_report(days=days),
        "latest_evaluation": latest.as_dict() if latest else None,
    }


@router.get("/cost-stop")
async def cost_stop_status() -> dict[str, Any]:
    return await cost_stop.status()
