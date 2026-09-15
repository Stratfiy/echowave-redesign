"""Reads and writes for installed skills. Org-scoped on every one."""

from collections.abc import Sequence
from typing import Optional

from sqlalchemy import delete, func, select

from api.db.base_client import BaseDBClient
from api.db.models import OrganisationSkillModel


class OrganisationSkillClient(BaseDBClient):
    async def list_organisation_skills(
        self,
        *,
        organization_id: int,
        workflow_id: Optional[int] = None,
    ) -> Sequence[OrganisationSkillModel]:
        """Every row for this account, or only those on one bot.

        Oldest first: the order skills were added is the order they are
        shown and the order their prompt blocks are joined, so a person
        who put the chasing procedure on before the tone one gets them
        that way round every time.
        """
        async with self.async_session() as session:
            query = select(OrganisationSkillModel).where(
                OrganisationSkillModel.organization_id == organization_id
            )
            if workflow_id is not None:
                query = query.where(OrganisationSkillModel.workflow_id == workflow_id)
            query = query.order_by(OrganisationSkillModel.id.asc())
            return (await session.execute(query)).scalars().all()

    async def count_skills_on_workflow(
        self, *, organization_id: int, workflow_id: int
    ) -> int:
        async with self.async_session() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(OrganisationSkillModel)
                    .where(
                        OrganisationSkillModel.organization_id == organization_id,
                        OrganisationSkillModel.workflow_id == workflow_id,
                    )
                )
                or 0
            )

    async def add_organisation_skill(
        self,
        *,
        organization_id: int,
        slug: str,
        workflow_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> None:
        """Keep a skill, or put it on a bot. Doing it twice changes nothing."""
        async with self.async_session() as session:
            existing = await session.scalar(
                select(OrganisationSkillModel.id).where(
                    OrganisationSkillModel.organization_id == organization_id,
                    OrganisationSkillModel.slug == slug,
                    OrganisationSkillModel.workflow_id.is_(workflow_id)
                    if workflow_id is None
                    else OrganisationSkillModel.workflow_id == workflow_id,
                )
            )
            if existing is not None:
                return
            session.add(
                OrganisationSkillModel(
                    organization_id=organization_id,
                    slug=slug,
                    workflow_id=workflow_id,
                    added_by_user_id=user_id,
                )
            )
            await session.commit()

    async def remove_organisation_skill(
        self,
        *,
        organization_id: int,
        slug: str,
        workflow_id: Optional[int] = None,
        all_rows: bool = False,
    ) -> int:
        """Take a skill off one bot, or off the account entirely."""
        async with self.async_session() as session:
            query = delete(OrganisationSkillModel).where(
                OrganisationSkillModel.organization_id == organization_id,
                OrganisationSkillModel.slug == slug,
            )
            if not all_rows:
                query = query.where(
                    OrganisationSkillModel.workflow_id.is_(None)
                    if workflow_id is None
                    else OrganisationSkillModel.workflow_id == workflow_id
                )
            result = await session.execute(query)
            await session.commit()
            return int(result.rowcount or 0)

    async def set_skill_bots(
        self,
        *,
        organization_id: int,
        slug: str,
        workflow_ids: Sequence[int],
        user_id: Optional[int] = None,
    ) -> None:
        """Put one skill on exactly these bots.

        The whole set in one transaction: a multi-select that removed three
        and then failed to add two would leave the account with neither the
        old answer nor the new one.

        The installed row is written too. A skill on a bot that was not
        installed is a skill the shelf cannot show under Installed, which is
        where somebody will look for it.
        """
        wanted = list(dict.fromkeys(int(w) for w in workflow_ids))
        async with self.async_session() as session:
            await session.execute(
                delete(OrganisationSkillModel).where(
                    OrganisationSkillModel.organization_id == organization_id,
                    OrganisationSkillModel.slug == slug,
                    OrganisationSkillModel.workflow_id.is_not(None),
                )
            )
            installed = await session.scalar(
                select(OrganisationSkillModel.id).where(
                    OrganisationSkillModel.organization_id == organization_id,
                    OrganisationSkillModel.slug == slug,
                    OrganisationSkillModel.workflow_id.is_(None),
                )
            )
            if installed is None:
                session.add(
                    OrganisationSkillModel(
                        organization_id=organization_id,
                        slug=slug,
                        workflow_id=None,
                        added_by_user_id=user_id,
                    )
                )
            for workflow_id in wanted:
                session.add(
                    OrganisationSkillModel(
                        organization_id=organization_id,
                        slug=slug,
                        workflow_id=workflow_id,
                        added_by_user_id=user_id,
                    )
                )
            await session.commit()
