"""Which accounts are Decibyl's own, and therefore have everything.

An account run by a superadmin is the company's account: the demo account,
the one a client is shown, the one a bug is reproduced on. Nothing on it is
a feature to sell, so no entitlement — the knowledge base, own vendor keys,
the verified-address gate — is withheld from it. The decision is made once
here rather than at each gate, so a new gate cannot forget it.

Staff standing is a property of the *account*, not only of the caller,
because half the gates run where there is no caller: the ingestion worker
enforces a per-file limit on an object already in the store, and it has an
organization id and nothing else. An account is staff if any superadmin is
a member of it.
"""

from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import OrganizationMembershipModel, UserModel
from api.enums import StaffRole


def is_superadmin(user) -> bool:
    """The caller's own standing, for gates that have a caller."""
    return getattr(user, "staff_role", None) == StaffRole.SUPERADMIN.value


async def is_staff_account(session: AsyncSession, *, organization_id: int) -> bool:
    """Whether a superadmin is a member of this organization."""
    stmt = select(
        exists().where(
            OrganizationMembershipModel.organization_id == organization_id,
            OrganizationMembershipModel.user_id == UserModel.id,
            UserModel.staff_role == StaffRole.SUPERADMIN.value,
        )
    )
    return bool((await session.execute(stmt)).scalar())
