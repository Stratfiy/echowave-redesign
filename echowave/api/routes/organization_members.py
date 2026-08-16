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

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.constants import UI_APP_URL
from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.schemas.organization_members import (
    OrganizationMemberResponse,
    OrganizationMembersListResponse,
    UpdateMemberRoleRequest,
)
from api.services.auth.depends import (
    get_user,
    get_user_with_selected_organization,
    require_organization_role,
)
from api.services.messaging.email import send_email
from api.services.organizations import invitations

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


# ---------------------------------------------------------------------------
# Invitations
#
# The piece the module docstring above says did not exist. It is written here
# rather than in a new router because it is the same subject — who is in this
# organization — and splitting "the roster" from "how you get on it" across two
# files is how the two drift apart.
#
# Issuing and revoking are Owner-only, matching the two routes above: deciding
# who is in the organization, and at what tier, is one job. Accepting is not,
# and cannot be — the person accepting is by definition not a member yet.
# ---------------------------------------------------------------------------


class InviteRequest(BaseModel):
    email: str = Field(..., description="Who to invite")
    role: str = Field(
        OrganizationRole.MEMBER.value,
        description="The seat they get. member, admin or owner.",
    )


class AcceptInvitationRequest(BaseModel):
    token: str = Field(..., description="The token from the invitation link")


def _invitation_view(invitation) -> dict[str, Any]:
    """Never includes the token. It exists in the clear exactly once, in the
    response to the call that created it, and is hashed everywhere else."""
    return {
        "id": invitation.id,
        "email": invitation.email,
        "role": invitation.role,
        "created_at": invitation.created_at.isoformat()
        if invitation.created_at
        else None,
        "expires_at": invitation.expires_at.isoformat(),
    }


@router.get("/invitations")
async def list_invitations(
    user: UserModel = Depends(get_user_with_selected_organization),
) -> dict[str, Any]:
    """Who has been offered a seat and not taken it yet.

    Readable by any member for the same reason the roster is: an invited
    person who has not accepted is otherwise invisible, which is how somebody
    gets invited twice.
    """
    async with db_client.async_session() as session:
        rows = await invitations.pending_for_organization(
            session, user.selected_organization_id
        )
        return {
            "invitations": [_invitation_view(r) for r in rows],
            "roles": invitations.role_choices(),
        }


@router.post("/invitations")
async def create_invitation(
    request: InviteRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.OWNER)),
) -> dict[str, Any]:
    """Offer somebody a seat, and mail them the link.

    The raw token comes back once, in this response, so the caller can show a
    copyable link. That matters more than it looks: on a deployment with no
    SMTP configured the email silently does not arrive, and without the link
    on screen the invitation would be unusable with no way to tell.
    """
    async with db_client.async_session() as session:
        try:
            invitation, raw_token = await invitations.invite(
                session,
                organization_id=user.selected_organization_id,
                email=request.email,
                role=request.role,
                invited_by=user.id,
            )
        except invitations.InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    link = f"{UI_APP_URL}/invitations/accept?token={quote(raw_token)}"

    # Best-effort, and deliberately after the commit. The invitation exists
    # either way; a mail server having a bad minute must not undo it, and the
    # link is in the response regardless.
    result = await send_email(
        to=invitation.email,
        subject="You have been invited to a Decibyl account",
        body_text=(
            f"{user.email or 'Someone'} has invited you to join their Decibyl "
            f"account as {invitation.role}.\n\n"
            f"Accept the invitation:\n{link}\n\n"
            "The link expires in seven days. If you were not expecting this, "
            "you can ignore it."
        ),
    )

    return {
        "invitation": _invitation_view(invitation),
        "link": link,
        "email_sent": result.ok,
        "email_error": None if result.ok else result.error,
    }


@router.delete("/invitations/{invitation_id}")
async def revoke_invitation(
    invitation_id: int,
    user: UserModel = Depends(require_organization_role(OrganizationRole.OWNER)),
) -> dict[str, Any]:
    """Withdraw an offer that has not been taken up."""
    async with db_client.async_session() as session:
        try:
            await invitations.revoke(
                session,
                organization_id=user.selected_organization_id,
                invitation_id=invitation_id,
            )
        except invitations.InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
    return {"revoked": True}


@router.get("/invitations/preview")
async def preview_invitation(token: str) -> dict[str, Any]:
    """What this link is for, before deciding to take it.

    Any authenticated user, because the person following the link is not a
    member yet. Returns the organization and the role and nothing that would
    let somebody enumerate accounts — the token is the only thing that
    resolves, and it is already in the caller's hands.
    """
    async with db_client.async_session() as session:
        try:
            invitation = await invitations.lookup(session, token)
        except invitations.InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        organization = await db_client.get_organization_by_id(
            invitation.organization_id
        )
        return {
            "email": invitation.email,
            "role": invitation.role,
            "organization_name": (
                getattr(organization, "billing_name", None) or "a Decibyl account"
            ),
            "expires_at": invitation.expires_at.isoformat(),
        }


@router.post("/invitations/accept")
async def accept_invitation(
    request: AcceptInvitationRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """Take the seat.

    Not gated on any organization role — the whole point is that the caller
    has none here yet. What it *is* gated on is the address: the invitation
    names one, and the caller has to be signed in as it.
    """
    async with db_client.async_session() as session:
        try:
            invitation = await invitations.accept(
                session, raw_token=request.token, user=await session.merge(user)
            )
        except invitations.InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        organization_id = invitation.organization_id
        role = invitation.role
        await session.commit()
    return {"organization_id": organization_id, "role": role}
