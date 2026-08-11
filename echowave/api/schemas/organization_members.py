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
