from pydantic import BaseModel, Field

from api.enums import OrganizationRole


class OrganizationMemberResponse(BaseModel):
    """One member of the current organization, as the team-management screen shows them."""

    user_id: int
    email: str | None
    role: str


class OrganizationMembersListResponse(BaseModel):
    members: list[OrganizationMemberResponse]


class UpdateMemberRoleRequest(BaseModel):
    role: OrganizationRole = Field(
        ..., description="The member's new role: member, admin, or owner."
    )


class InviteMemberRequest(BaseModel):
    """Add somebody to this organization by email.

    No password field, deliberately. An invited account is created without one
    and stays unreachable by password until its owner sets theirs — inventing a
    temporary password would put a working credential in an inbox, in the
    clear, for as long as the mailbox exists.
    """

    email: str = Field(..., max_length=320, description="Where the invitation goes.")
    role: OrganizationRole = Field(
        default=OrganizationRole.MEMBER,
        description="The role they get on accepting.",
    )


class InviteMemberResponse(BaseModel):
    email: str
    #: False when the address already had an account with no password — a
    #: previous invitation, or a Google-only sign-in — so the screen can say
    #: "re-sent" rather than "created".
    is_new_user: bool
    expires_at: str
    #: A failed send is reported rather than swallowed. The invitation is
    #: staged either way, and an inviter who is not told will sit waiting for
    #: somebody to accept an email that never arrived.
    email_sent: bool
    email_error: str | None = None
