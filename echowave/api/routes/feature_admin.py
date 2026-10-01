"""Staff console for feature switches (ADMIN-1).

Turn a feature on or off for one organisation, or for everyone, without SSH,
an ``.env`` edit and a restart. Superadmin only, and out of the public
OpenAPI (``/admin/`` path and ``admin-`` tag; see services/openapi_surface.py).

Each write is audited in ``admin_action_log`` and reaches every worker within
seconds (see ``services/feature_admin.publish_change``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services import feature_admin
from api.services.auth.depends import get_superuser

router = APIRouter(
    prefix="/admin/features",
    tags=["admin-features"],
    dependencies=[Depends(get_superuser)],
)


class OrganizationOverrideRequest(BaseModel):
    enabled: bool
    note: str | None = Field(default=None, max_length=300)
    #: After this the override stops applying, as if it had been removed.
    expires_at: datetime | None = None


class GlobalOverrideRequest(BaseModel):
    enabled: bool
    #: Must repeat the flag's name: a global flip reaches every account.
    confirm: str
    note: str | None = Field(default=None, max_length=300)
    expires_at: datetime | None = None


def _not_found(exc: LookupError) -> HTTPException:
    if isinstance(exc, feature_admin.UnknownFeature):
        return HTTPException(status_code=404, detail=f"Unknown feature {exc.args[0]}")
    return HTTPException(status_code=404, detail="No such organization")


def _require_confirm(name: str, confirm: str | None) -> None:
    if (confirm or "").strip() != name:
        raise HTTPException(
            status_code=400,
            detail=f"Type the flag's name ({name}) to change it for everyone",
        )


@router.get("")
async def list_features() -> dict[str, Any]:
    """Every flag, its global value and where that comes from, and every
    override."""
    async with db_client.async_session() as session:
        return {"flags": await feature_admin.registry(session)}


@router.get("/organizations/{organization_id}")
async def organization_features(organization_id: int) -> dict[str, Any]:
    """Each flag as one organisation sees it, with the override deciding it."""
    async with db_client.async_session() as session:
        try:
            flags = await feature_admin.flags_for_organization(session, organization_id)
        except LookupError as exc:
            raise _not_found(exc) from exc
    return {"organization_id": organization_id, "flags": flags}


@router.put("/{name}/organizations/{organization_id}")
async def set_organization_override(
    name: str,
    organization_id: int,
    body: OrganizationOverrideRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Force ``name`` on or off for one organisation."""
    async with db_client.async_session() as session:
        try:
            await feature_admin.set_override(
                session,
                name=name,
                organization_id=organization_id,
                enabled=body.enabled,
                note=body.note,
                expires_at=body.expires_at,
                actor_user_id=user.id,
            )
        except LookupError as exc:
            raise _not_found(exc) from exc
        await session.commit()
    await feature_admin.publish_change(organization_id)
    return {"name": name, "organization_id": organization_id, "enabled": body.enabled}


@router.delete("/{name}/organizations/{organization_id}")
async def clear_organization_override(
    name: str,
    organization_id: int,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Remove the organisation's override; the global value applies again."""
    async with db_client.async_session() as session:
        try:
            removed = await feature_admin.clear_override(
                session,
                name=name,
                organization_id=organization_id,
                actor_user_id=user.id,
            )
        except LookupError as exc:
            raise _not_found(exc) from exc
        await session.commit()
    await feature_admin.publish_change(organization_id)
    return {"name": name, "organization_id": organization_id, "removed": removed}


@router.put("/{name}/global")
async def set_global_override(
    name: str,
    body: GlobalOverrideRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Turn ``name`` on or off for everyone. Overrides the environment."""
    async with db_client.async_session() as session:
        try:
            feature_admin.require_known(name)
        except LookupError as exc:
            raise _not_found(exc) from exc
        _require_confirm(name, body.confirm)
        await feature_admin.set_override(
            session,
            name=name,
            organization_id=None,
            enabled=body.enabled,
            note=body.note,
            expires_at=body.expires_at,
            actor_user_id=user.id,
        )
        await session.commit()
    await feature_admin.publish_change(None)
    return {"name": name, "organization_id": None, "enabled": body.enabled}


@router.delete("/{name}/global")
async def clear_global_override(
    name: str,
    confirm: str = Query(..., description="The flag's name, typed again"),
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Drop the console's global switch; the environment decides again."""
    async with db_client.async_session() as session:
        try:
            feature_admin.require_known(name)
        except LookupError as exc:
            raise _not_found(exc) from exc
        _require_confirm(name, confirm)
        removed = await feature_admin.clear_override(
            session, name=name, organization_id=None, actor_user_id=user.id
        )
        await session.commit()
    await feature_admin.publish_change(None)
    return {"name": name, "organization_id": None, "removed": removed}
