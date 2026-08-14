"""Membership and roles within the caller's currently-selected organization.

Membership used to be mirror-only: in SaaS mode it arrives from Stack Auth team
membership on login (see ``get_user`` in api/services/auth/depends.py), and
Stack Auth owns invites at the team level. That left an Owner on a local
deployment able to promote, demote and remove people — but never to *add* one,
which made "manage your team" a screen you could only ever subtract from.

``POST /members`` closes that. It creates the user row with no password at all
and emails a code, because ``login`` refuses any account whose
``password_hash`` is falsy and inventing a temporary one would put a working
credential in an inbox in the clear. See ``services/auth/invitations.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.schemas.organization_members import (
    InviteMemberRequest,
    InviteMemberResponse,
    OrganizationMemberResponse,
    OrganizationMembersListResponse,
    UpdateMemberRoleRequest,
)
from api.services.auth import invitations
from api.services.auth.depends import (
    get_user_with_selected_organization,
    require_organization_role,
)
from api.services.messaging.email import send_email

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


@router.post("/members", response_model=InviteMemberResponse)
async def invite_member(
    request: InviteMemberRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.OWNER)),
) -> InviteMemberResponse:
    """Invite somebody into this organization by email. Owner-only.

    Owner-only for the same reason promoting is: adding a member grants
    standing access to every workflow, recording and phone number the account
    holds, and that is not a decision a member should be able to make.

    The membership row is created *on acceptance*, not here. Creating it now
    would put someone in the organization who cannot sign in, which shows up on
    the roster as a member nobody can contact and nobody can explain.
    """
    organization_id = user.selected_organization_id

    async with db_client.async_session() as session:
        try:
            issued = await invitations.issue(
                session,
                email=request.email,
                organization_id=organization_id,
                invited_by=user.id,
                role=request.role.value,
            )
        except invitations.InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    result = await send_email(
        to=issued.email,
        subject=invitations.notice_subject(),
        body_text=invitations.notice_body(issued, invited_by_email=user.email),
    )
    return InviteMemberResponse(
        email=issued.email,
        is_new_user=issued.is_new_user,
        expires_at=issued.expires_at.isoformat(),
        email_sent=result.ok,
        email_error=result.error,
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
