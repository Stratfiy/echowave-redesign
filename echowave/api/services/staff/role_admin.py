"""Granting and revoking console roles (screen 44), as staff commands.

Owner only. A grant previews the exact capabilities it adds; only an
existing staff member can hold a console role, so the console cannot make
anyone staff (that stays the deliberate superadmin step it is today).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import UserModel
from api.db.staff_models import StaffRoleGrantModel
from api.services.staff import commands
from api.services.staff import roles as staff_roles


class RoleTarget(commands.Target):
    user_id: int = Field(gt=0)
    role: Literal["support", "operations", "finance", "quality"]


async def _live_grant(
    session: AsyncSession, user_id: int, role: str
) -> StaffRoleGrantModel | None:
    return (
        await session.execute(
            select(StaffRoleGrantModel).where(
                StaffRoleGrantModel.user_id == user_id,
                StaffRoleGrantModel.role == role,
                StaffRoleGrantModel.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def _grant_eligible(session: AsyncSession, t: RoleTarget) -> str | None:
    user = await session.get(UserModel, t.user_id)
    if user is None:
        return "There is no such user."
    if not staff_roles.tier_roles(user):
        return "Console roles are only for staff; this person is not staff."
    if t.role in staff_roles.tier_roles(user):
        return f"This person already has {t.role} through their staff tier."
    if await _live_grant(session, t.user_id, t.role):
        return f"This person already holds {t.role}."
    return None


async def _current_roles(session: AsyncSession, user: UserModel) -> set[str]:
    rows = await session.execute(
        select(StaffRoleGrantModel.role).where(
            StaffRoleGrantModel.user_id == user.id,
            StaffRoleGrantModel.revoked_at.is_(None),
        )
    )
    return staff_roles.tier_roles(user) | {r for (r,) in rows.all()}


async def _grant_preview(session: AsyncSession, t: RoleTarget) -> dict:
    user = await session.get(UserModel, t.user_id)
    before = await _current_roles(session, user)
    after = before | {t.role}
    gained = sorted(
        set(staff_roles.capabilities_for(after))
        - set(staff_roles.capabilities_for(before))
    )
    return {
        "user": {"id": user.id, "email": user.email, "tier": user.staff_role},
        "roles_before": sorted(before),
        "roles_after": sorted(after),
        "capabilities_added": gained,
        "applies": "On the person's next request; no sign-out needed.",
    }


async def _grant(
    session: AsyncSession, t: RoleTarget, actor: commands.Actor
) -> commands.Outcome:
    if await _live_grant(session, t.user_id, t.role):
        return commands.Outcome({"message": "Already held; nothing changed."})
    command = await session.get(commands.StaffCommandModel, actor.command_id)
    row = StaffRoleGrantModel(
        user_id=t.user_id,
        role=t.role,
        reason=command.reason if command else "",
        granted_by=actor.approved_by or actor.requested_by,
        command_id=actor.command_id,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return commands.Outcome({"grant_id": row.id, "message": f"{t.role} granted."})


async def _revoke_eligible(session: AsyncSession, t: RoleTarget) -> str | None:
    if await _live_grant(session, t.user_id, t.role) is None:
        return f"This person does not hold {t.role} through a grant."
    return None


async def _revoke_preview(session: AsyncSession, t: RoleTarget) -> dict:
    user = await session.get(UserModel, t.user_id)
    before = await _current_roles(session, user)
    after = before - {t.role}
    lost = sorted(
        set(staff_roles.capabilities_for(before))
        - set(staff_roles.capabilities_for(after))
    )
    return {
        "user": {"id": user.id, "email": user.email, "tier": user.staff_role},
        "roles_before": sorted(before),
        "roles_after": sorted(after),
        "capabilities_removed": lost,
        "applies": "On the person's next request.",
    }


async def _revoke(
    session: AsyncSession, t: RoleTarget, actor: commands.Actor
) -> commands.Outcome:
    grant = await _live_grant(session, t.user_id, t.role)
    if grant is None:
        return commands.Outcome({"message": "Not held; nothing changed."})
    grant.revoked_at = datetime.now(UTC)
    grant.revoked_by = actor.approved_by or actor.requested_by
    return commands.Outcome({"grant_id": grant.id, "message": f"{t.role} revoked."})


commands.register(
    commands.CommandSpec(
        name="role.grant",
        summary="Give a staff member a console role.",
        request_capability="roles.manage",
        target=RoleTarget,
        handler=_grant,
        feature="staff_roles",
        eligible=_grant_eligible,
        preview=_grant_preview,
    )
)
commands.register(
    commands.CommandSpec(
        name="role.revoke",
        summary="Take a console role away from a staff member.",
        request_capability="roles.manage",
        target=RoleTarget,
        handler=_revoke,
        feature="staff_roles",
        eligible=_revoke_eligible,
        preview=_revoke_preview,
    )
)


async def staff_members(session: AsyncSession) -> list[dict]:
    """Every staff member, their tier, live grants and effective roles."""
    users = (
        (
            await session.execute(
                select(UserModel)
                .where(UserModel.staff_role.isnot(None))
                .order_by(UserModel.id)
            )
        )
        .scalars()
        .all()
    )
    grants = (
        (
            await session.execute(
                select(StaffRoleGrantModel).where(
                    StaffRoleGrantModel.revoked_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    by_user: dict[int, list[StaffRoleGrantModel]] = {}
    for g in grants:
        by_user.setdefault(g.user_id, []).append(g)
    out = []
    for u in users:
        held = by_user.get(u.id, [])
        out.append(
            {
                "user_id": u.id,
                "email": u.email,
                "tier": u.staff_role,
                "mfa_enabled": bool(u.mfa_enabled),
                "tier_roles": sorted(staff_roles.tier_roles(u)),
                "grants": [
                    {
                        "id": g.id,
                        "role": g.role,
                        "reason": g.reason,
                        "granted_by": g.granted_by,
                        "created_at": g.created_at.isoformat(),
                    }
                    for g in held
                ],
            }
        )
    return out
