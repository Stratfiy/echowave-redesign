"""A personal space for every person, beside the workspaces they join.

The founder's "person + business as one", and handoff 8: personal context
is private to the person unless explicitly shared, and a work agent must
not inherit a person's personal email or learning history. See
``CONTROLS.md`` ("Personal space") for the decision and the alternatives.

**The model.** A personal space is an ``organizations`` row with
``kind='personal'`` and ``personal_owner_user_id`` set, and exactly one
membership: its owner, as owner. That is the whole trick. Everything in the
product is already scoped by ``organization_id`` -- memory, threads, tasks,
files, connections -- so everything a person keeps in their personal space
is theirs by the same rule that keeps one business from another, and no
existing filter is loosened to make it so. Their memory and private threads
there belong to them; switching to a business workspace switches to that
workspace's scope, where the personal space is just another tenant.

**The guard.** A database trigger (migration ``202610071800controls``)
refuses any membership in a personal space other than its owner's, so no
path -- an invitation, the team screen, a login re-sync -- can add a second
reader. ``assert_not_personal`` gives the friendly refusal before that.

**Created lazily.** The first ``ensure`` makes it; no backfill. It is not a
signup: it gets no API key and no signup credit (a second free grant per
person would be a way to mint credit), and its owner's selected workspace
is not changed unless they switch to it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.models import OrganizationMembershipModel, OrganizationModel
from api.enums import OrganizationRole
from api.services import features

FLAG = "personal_space"
KIND = "personal"
NAME = "Personal"


def enabled() -> bool:
    return features.is_on(FLAG)


class PersonalSpaceError(ValueError):
    pass


def _provider_id(user_id: int) -> str:
    return f"personal:{user_id}"


async def find(user_id: int) -> OrganizationModel | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(OrganizationModel).where(
                OrganizationModel.personal_owner_user_id == user_id,
                OrganizationModel.kind == KIND,
            )
        )


async def ensure(user_id: int) -> OrganizationModel:
    """The person's personal space, made the first time it is asked for.

    Safe under a race: the insert is ON CONFLICT DO NOTHING on the unique
    provider id, and the membership likewise, so two first requests end
    with one space and one membership.
    """
    existing = await find(user_id)
    if existing is not None:
        return existing
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        await session.execute(
            insert(OrganizationModel.__table__)
            .values(
                provider_id=_provider_id(user_id),
                name=NAME,
                kind=KIND,
                personal_owner_user_id=user_id,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["provider_id"])
        )
        space = await session.scalar(
            select(OrganizationModel).where(
                OrganizationModel.provider_id == _provider_id(user_id)
            )
        )
        if space is None or space.personal_owner_user_id != user_id:
            raise PersonalSpaceError("Could not make a personal space.")
        await session.execute(
            insert(OrganizationMembershipModel)
            .values(
                user_id=user_id,
                organization_id=space.id,
                role=OrganizationRole.OWNER.value,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "organization_id"])
        )
        await session.commit()
        await session.refresh(space)
        return space


def is_personal(organization: object | None) -> bool:
    return getattr(organization, "kind", None) == KIND


async def is_personal_id(organization_id: int | None) -> bool:
    if not organization_id:
        return False
    async with db_client.async_session() as session:
        kind = await session.scalar(
            select(OrganizationModel.kind).where(
                OrganizationModel.id == organization_id
            )
        )
    return kind == KIND


async def assert_not_personal(organization_id: int | None) -> None:
    """Refuse, in words, to add anyone to a personal space. The trigger is
    the guarantee; this is the sentence a person reads."""
    if await is_personal_id(organization_id):
        raise PersonalSpaceError(
            "This is a personal space. Only its owner can be in it; to work "
            "with others, use a workspace."
        )


def describe(space: OrganizationModel) -> dict:
    return {
        "organization_id": space.id,
        "name": space.name or NAME,
        "kind": KIND,
        "owner_user_id": space.personal_owner_user_id,
    }
