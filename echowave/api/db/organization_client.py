from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import exists, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import (
    APIKeyModel,
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
)
from api.enums import OrganizationRole
from api.utils.api_key import generate_api_key


@dataclass(frozen=True)
class OrganizationMember:
    """A member as the org's team-management screen shows them."""

    user: UserModel
    role: str


class OrganizationClient(BaseDBClient):
    async def get_organization_by_id(
        self, organization_id: int
    ) -> Optional[OrganizationModel]:
        """Get an organization by its ID."""
        async with self.async_session() as session:
            result = await session.execute(
                select(OrganizationModel).where(OrganizationModel.id == organization_id)
            )
            return result.scalars().first()

    async def get_organization_users(self, organization_id: int) -> list[UserModel]:
        """Get all users linked to an organization, regardless of role."""
        async with self.async_session() as session:
            result = await session.execute(
                select(UserModel)
                .join(
                    OrganizationMembershipModel,
                    OrganizationMembershipModel.user_id == UserModel.id,
                )
                .where(OrganizationMembershipModel.organization_id == organization_id)
                .order_by(UserModel.id)
            )
            return list(result.scalars().all())

    async def list_organization_members(
        self, organization_id: int
    ) -> list[OrganizationMember]:
        """Members with their role — what a team-management screen shows."""
        async with self.async_session() as session:
            rows = (
                await session.execute(
                    select(UserModel, OrganizationMembershipModel.role)
                    .join(
                        OrganizationMembershipModel,
                        OrganizationMembershipModel.user_id == UserModel.id,
                    )
                    .where(
                        OrganizationMembershipModel.organization_id == organization_id
                    )
                    .order_by(UserModel.id)
                )
            ).all()
            return [OrganizationMember(user=user, role=role) for user, role in rows]

    async def get_membership(
        self, user_id: int, organization_id: int
    ) -> Optional[OrganizationMembershipModel]:
        """One user's membership row in one organization, or None."""
        async with self.async_session() as session:
            return await session.scalar(
                select(OrganizationMembershipModel).where(
                    OrganizationMembershipModel.user_id == user_id,
                    OrganizationMembershipModel.organization_id == organization_id,
                )
            )

    async def get_or_create_organization_by_provider_id(
        self, org_provider_id: str, user_id: int
    ) -> tuple[OrganizationModel, bool]:
        """Get an existing organization by provider_id or create a new one.

        Returns:
            A tuple of (organization, was_created) where was_created is True if the organization
            was created in this call, False if it already existed.
        """
        async with self.async_session() as session:
            # First try to get existing organization
            result = await session.execute(
                select(OrganizationModel).where(
                    OrganizationModel.provider_id == org_provider_id
                )
            )
            organization = result.scalars().first()

            if organization is None:
                # Use PostgreSQL's INSERT ... ON CONFLICT DO NOTHING
                # This is atomic and handles race conditions at the database level

                stmt = insert(OrganizationModel.__table__).values(
                    provider_id=org_provider_id, created_at=datetime.now(timezone.utc)
                )
                # ON CONFLICT DO NOTHING - if another request already inserted, this becomes a no-op
                stmt = stmt.on_conflict_do_nothing(index_elements=["provider_id"])

                result = await session.execute(stmt)
                await session.commit()

                # Check if we actually inserted (rowcount > 0) or if there was a conflict (rowcount == 0)
                was_created = result.rowcount > 0

                # Now fetch the organization (either the one we just created or the one that existed)
                result = await session.execute(
                    select(OrganizationModel).where(
                        OrganizationModel.provider_id == org_provider_id
                    )
                )
                organization = result.scalars().first()

                if organization is None:
                    # This should never happen, but handle it just in case
                    error_msg = f"Failed to create or fetch organization with provider_id {org_provider_id}"
                    raise ValueError(error_msg)

                # Read before the commit below. A commit expires every loaded
                # attribute, and reading one afterwards makes SQLAlchemy try to
                # reload it — an implicit IO that raises in async context rather
                # than quietly fetching.
                organization_id = organization.id

                # Only create API key if we actually created the organization
                if was_created:
                    # Create a default API key for the new organization
                    _, key_hash, key_prefix = generate_api_key()

                    api_key = APIKeyModel(
                        organization_id=organization_id,
                        name="Default API Key",
                        key_hash=key_hash,
                        key_prefix=key_prefix,
                        is_active=True,
                        created_by=user_id,
                    )
                    session.add(api_key)
                    await session.commit()

                    # Free credit, so a new account's first five minutes are not
                    # "prepaid account with no credit cannot make a call".
                    # Committed separately and never allowed to propagate: a
                    # failed bonus must not take down the signup that earned it.
                    try:
                        from api.services.billing.signup_bonus import (
                            grant_signup_bonus,
                        )

                        granted = await grant_signup_bonus(
                            session, organization_id=organization_id
                        )
                        if granted:
                            await session.commit()
                    except Exception as e:
                        logger.error(
                            "Could not grant a signup bonus to org {}: {}",
                            organization_id,
                            e,
                        )
                        await session.rollback()

                await session.refresh(organization)
                return organization, was_created
            return organization, False

    async def is_user_member_of_organization(
        self, user_id: int, organization_id: int
    ) -> bool:
        """Return True if the user belongs to the given organization."""
        async with self.async_session() as session:
            result = await session.execute(
                select(
                    exists().where(
                        (OrganizationMembershipModel.user_id == user_id)
                        & (
                            OrganizationMembershipModel.organization_id
                            == organization_id
                        )
                    )
                )
            )
            return bool(result.scalar())

    async def add_user_to_organization(
        self,
        user_id: int,
        organization_id: int,
        role: str = OrganizationRole.MEMBER.value,
    ) -> None:
        """Ensure that a user has a membership row in an organization.

        Created only if it does not already exist — an existing membership's
        role is left untouched (use ``update_member_role`` to change it), so a
        re-sync (e.g. Stack Auth login re-confirming team membership) can
        never quietly reset someone back to MEMBER.
        """
        async with self.async_session() as session:
            stmt = insert(OrganizationMembershipModel).values(
                user_id=user_id, organization_id=organization_id, role=role
            )
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["user_id", "organization_id"]
            )
            await session.execute(stmt)
            await session.commit()

    async def update_member_role(
        self, user_id: int, organization_id: int, role: str
    ) -> Optional[OrganizationMembershipModel]:
        """Change a member's role. Returns None if they aren't a member."""
        async with self.async_session() as session:
            membership = await session.scalar(
                select(OrganizationMembershipModel).where(
                    OrganizationMembershipModel.user_id == user_id,
                    OrganizationMembershipModel.organization_id == organization_id,
                )
            )
            if membership is None:
                return None
            membership.role = role
            await session.commit()
            await session.refresh(membership)
            return membership

    async def remove_user_from_organization(
        self, user_id: int, organization_id: int
    ) -> bool:
        """Remove a member. Returns False if they weren't a member."""
        async with self.async_session() as session:
            membership = await session.scalar(
                select(OrganizationMembershipModel).where(
                    OrganizationMembershipModel.user_id == user_id,
                    OrganizationMembershipModel.organization_id == organization_id,
                )
            )
            if membership is None:
                return False
            await session.delete(membership)
            await session.commit()
            return True

    async def count_organization_owners(self, organization_id: int) -> int:
        """How many Owners an organization has.

        Used to refuse demoting or removing the last one — an organization
        with no Owner has no one able to manage membership or restore an
        Owner, which is a dead end this API should never produce.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(OrganizationMembershipModel.id)).where(
                    OrganizationMembershipModel.organization_id == organization_id,
                    OrganizationMembershipModel.role == OrganizationRole.OWNER.value,
                )
            )
            return int(result.scalar() or 0)
