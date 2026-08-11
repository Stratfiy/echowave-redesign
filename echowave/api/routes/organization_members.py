"""Membership and roles within the caller's currently-selected organization.

There is deliberately no invite-by-email endpoint here — in SaaS mode
membership is mirrored from Stack Auth team membership on login (see
``get_user`` in api/services/auth/depends.py), and Stack Auth owns invites at
the team level. What this file adds is the piece that didn't exist at all
before: once someone is a member, an Owner can see who else is, and promote,
demote, or remove them, instead of every member having identical access
forever with no local knob to turn.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.schemas.organization_members import (
    OrganizationMemberResponse,
    OrganizationMembersListResponse,
    UpdateMemberRoleRequest,
)
from api.services.auth.depends import (
    get_user_with_selected_organization,
    require_organization_role,
)

router = APIRouter(prefix="/organizations", tags=["organization-members"])


@router.get("/members", response_model=OrganizationMembersListResponse)
async def list_members(
    user: UserModel = Depends(get_user_with_selected_organization),
) -> OrganizationMembersListResponse:
    """Every member of the caller's organization, with their role.

    Any member can see the roster — it's who else is in the account, not a
    secret — but only an Owner can change it (see the two routes below).
    """
    members = await db_client.list_organization_members(user.selected_organization_id)
    return OrganizationMembersListResponse(
        members=[
            OrganizationMemberResponse(
                user_id=m.user.id, email=m.user.email, role=m.role
            )
            for m in members
        ]
    )


@router.patch("/members/{user_id}", response_model=OrganizationMemberResponse)
async def update_member_role(
    user_id: int,
    request: UpdateMemberRoleRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.OWNER)),
) -> OrganizationMemberResponse:
    """Promote or demote a member. Owner-only.

    Refuses to demote the organization's last remaining Owner — that would
    leave the account with no one able to manage membership at all, a dead
    end this API should never produce.
    """
    organization_id = user.selected_organization_id
    target = await db_client.get_membership(user_id, organization_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Not a member of this organization")

    if (
        target.role == OrganizationRole.OWNER.value
        and request.role != OrganizationRole.OWNER
        and await db_client.count_organization_owners(organization_id) <= 1
    ):
        raise HTTPException(
            status_code=400,
            detail="Cannot demote the last owner. Promote someone else first.",
        )

    updated = await db_client.update_member_role(
        user_id, organization_id, request.role.value
    )
    target_user = await db_client.get_user_by_id(user_id)
    return OrganizationMemberResponse(
        user_id=user_id,
        email=target_user.email if target_user else None,
        role=updated.role,
    )


@router.delete("/members/{user_id}")
async def remove_member(
    user_id: int,
    user: UserModel = Depends(require_organization_role(OrganizationRole.OWNER)),
) -> dict[str, str]:
    """Remove a member from the organization. Owner-only.

    Refuses to remove the last remaining Owner, for the same reason
    ``update_member_role`` refuses to demote one.
    """
    organization_id = user.selected_organization_id
    target = await db_client.get_membership(user_id, organization_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Not a member of this organization")

    if (
        target.role == OrganizationRole.OWNER.value
        and await db_client.count_organization_owners(organization_id) <= 1
    ):
        raise HTTPException(status_code=400, detail="Cannot remove the last owner.")

    await db_client.remove_user_from_organization(user_id, organization_id)
    return {"status": "removed"}
