"""A workspace's own roles (MP-2) and sharing them (MP-3).

Thin: the rules live in ``services/workspace_roles``. Every route resolves
the organization from the session, never the body, and is a 404 while
WORKSPACE_ROLES_ENABLED is off. Saving, hiring and installing are a
member's; sharing a role outside the workspace -- its prompts carry the
business's own answers -- is an admin's.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api import constants
from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services import features, workspace_roles
from api.services.auth.depends import require_organization_role

router = APIRouter(
    prefix="/workspace-roles",
    tags=["workspace-roles"],
    dependencies=[Depends(features.require("workspace_roles"))],
)


_member = require_organization_role(OrganizationRole.MEMBER)
_admin = require_organization_role(OrganizationRole.ADMIN)


class SaveRequest(BaseModel):
    workflow_id: int
    name: str | None = Field(None, max_length=128)
    summary: str | None = Field(None, max_length=1000)


class HireRequest(BaseModel):
    agent_name: str | None = Field(None, max_length=128)


class CopyRequest(BaseModel):
    organization_id: int


@router.get("")
async def list_workspace_roles(user: UserModel = Depends(_member)) -> dict[str, Any]:
    async with db_client.async_session() as session:
        roles = await workspace_roles.list_for(
            session, organization_id=user.selected_organization_id
        )
    return {"roles": roles}


@router.post("")
async def save_workspace_role(
    request: SaveRequest, user: UserModel = Depends(_member)
) -> dict[str, Any]:
    """Save an agent in this workspace as one of its roles."""
    async with db_client.async_session() as session:
        role = await workspace_roles.save_from_workflow(
            session,
            organization_id=user.selected_organization_id,
            user_id=user.id,
            workflow_id=request.workflow_id,
            name=request.name,
            summary=request.summary,
        )
        await session.commit()
    return {"role": role}


@router.delete("/{role_id}")
async def delete_workspace_role(role_id: int, user: UserModel = Depends(_member)):
    async with db_client.async_session() as session:
        removed = await workspace_roles.remove(
            session, organization_id=user.selected_organization_id, role_id=role_id
        )
        await session.commit()
    if not removed:
        raise HTTPException(status_code=404, detail="Not Found")
    return {"removed": True}


@router.post("/{role_id}/hire")
async def hire_workspace_role(
    role_id: int, request: HireRequest | None = None, user: UserModel = Depends(_member)
) -> dict[str, Any]:
    """A fresh agent from the role, answering nothing twice."""
    async with db_client.async_session() as session:
        return await workspace_roles.hire(
            session,
            organization_id=user.selected_organization_id,
            user_id=user.id,
            role_id=role_id,
            agent_name=(request.agent_name if request else None),
        )


@router.post("/{role_id}/share")
async def share_workspace_role(role_id: int, user: UserModel = Depends(_admin)):
    """A share link, shown once. Sharing again turns the last link off."""
    async with db_client.async_session() as session:
        token = await workspace_roles.share(
            session, organization_id=user.selected_organization_id, role_id=role_id
        )
        await session.commit()
    return {
        "token": token,
        "url": f"{constants.UI_APP_URL.rstrip('/')}/roles/install?token={token}",
    }


@router.delete("/{role_id}/share")
async def unshare_workspace_role(role_id: int, user: UserModel = Depends(_admin)):
    async with db_client.async_session() as session:
        await workspace_roles.unshare(
            session, organization_id=user.selected_organization_id, role_id=role_id
        )
        await session.commit()
    return {"shared": False}


@router.post("/{role_id}/copy-to")
async def copy_workspace_role(
    role_id: int, request: CopyRequest, user: UserModel = Depends(_admin)
) -> dict[str, Any]:
    """Into another workspace this person belongs to -- a client's."""
    async with db_client.async_session() as session:
        role = await workspace_roles.copy_to(
            session,
            organization_id=user.selected_organization_id,
            role_id=role_id,
            target_organization_id=request.organization_id,
            user_id=user.id,
        )
        await session.commit()
    return {"role": role}


@router.get("/shared/{token}")
async def preview_shared_role(token: str, user: UserModel = Depends(_member)):
    """What a link offers, and what this workspace would have to connect."""
    async with db_client.async_session() as session:
        try:
            return await workspace_roles.preview(session, token=token)
        except workspace_roles.RoleError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/shared/{token}/install")
async def install_shared_role(token: str, user: UserModel = Depends(_member)):
    async with db_client.async_session() as session:
        role = await workspace_roles.install(
            session,
            token=token,
            organization_id=user.selected_organization_id,
            user_id=user.id,
        )
        await session.commit()
    return {"role": role}
