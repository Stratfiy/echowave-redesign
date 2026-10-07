"""Who on the staff can do what in the console (handoff 32, screen 44).

The handoff names five roles: owner manages roles and budgets; support
handles permitted customer cases; operations manages runbooks; finance
handles refunds and reconciliation; quality manages evaluation datasets.

The two existing staff tiers map onto them: a superadmin is an owner, a
support-tier person is support. With ``staff_roles`` on, an owner can grant
any staff member operations, finance, quality or support on top (rows in
``staff_role_grants``, granted through the ``role.grant`` command). Off,
only the tier counts, so turning the flag off takes every grant away at once.

The matrix below is the one answer. Every console route asks ``require``
for a capability, so a hidden button never stands in for authorization:
the backend refuses support a refund however the request is made.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import select

from api.db import db_client
from api.db.models import UserModel
from api.db.staff_models import StaffRoleGrantModel
from api.enums import StaffRole
from api.services import features
from api.services.auth.depends import get_staff


class ConsoleRole(str, Enum):
    OWNER = "owner"
    SUPPORT = "support"
    OPERATIONS = "operations"
    FINANCE = "finance"
    QUALITY = "quality"


OWNER = ConsoleRole.OWNER.value
SUPPORT = ConsoleRole.SUPPORT.value
OPERATIONS = ConsoleRole.OPERATIONS.value
FINANCE = ConsoleRole.FINANCE.value
QUALITY = ConsoleRole.QUALITY.value
ALL_ROLES = (OWNER, SUPPORT, OPERATIONS, FINANCE, QUALITY)

#: Roles an owner can grant. Owner itself is the superadmin tier and is not
#: handed out from the console: making someone a superadmin stays the
#: deliberate, out-of-band step it is today.
GRANTABLE = (SUPPORT, OPERATIONS, FINANCE, QUALITY)

#: Capability -> the roles that hold it. Explicit rows rather than "owner
#: can do everything": refunds are finance-only (handoff 38), so an owner who
#: needs to refund grants themselves finance, and that grant is audited.
CAPABILITIES: dict[str, tuple[str, ...]] = {
    "overview.read": ALL_ROLES,
    "users.read": (OWNER, SUPPORT, OPERATIONS),
    "users.invite": (OWNER, SUPPORT),
    "users.suspend.request": (OWNER, SUPPORT),
    "users.suspend.approve": (OWNER,),
    "users.allowance": (OWNER, SUPPORT),
    "support.read": (OWNER, SUPPORT),
    "quality.read": (OWNER, QUALITY, OPERATIONS),
    "quality.manage": (OWNER, QUALITY),
    "analytics.read": ALL_ROLES,
    "revenue.read": (OWNER, FINANCE),
    "refunds.request": (FINANCE,),
    "refunds.approve": (FINANCE,),
    "operations.read": (OWNER, OPERATIONS, SUPPORT),
    "operations.act": (OWNER, OPERATIONS),
    "incidents.manage": (OWNER, OPERATIONS),
    "providers.read": (OWNER, OPERATIONS),
    "providers.rotate": (OWNER,),
    "policy.read": (OWNER, OPERATIONS, FINANCE),
    "policy.change": (OWNER,),
    "roles.manage": (OWNER,),
    "audit.read": (OWNER,),
}

#: What each capability means, for the matrix on screen 44.
CAPABILITY_LABELS: dict[str, str] = {
    "overview.read": "See the founder overview",
    "users.read": "See users, workspaces and invitations",
    "users.invite": "Issue and revoke invitations",
    "users.suspend.request": "Ask to suspend an account",
    "users.suspend.approve": "Approve a suspension",
    "users.allowance": "Grant a temporary daily allowance",
    "support.read": "Work support cases",
    "quality.read": "See evaluation runs and cases",
    "quality.manage": "Edit cases and start runs",
    "analytics.read": "See product analytics",
    "revenue.read": "See revenue, costs and the ledger",
    "refunds.request": "Ask for a refund",
    "refunds.approve": "Approve a refund",
    "operations.read": "See jobs, providers, delivery and traces",
    "operations.act": "Pause, retry or drain through approved commands",
    "incidents.manage": "Open and run incidents",
    "providers.read": "See provider key metadata",
    "providers.rotate": "Stage and rotate provider keys",
    "policy.read": "See flags, budgets and model policy",
    "policy.change": "Change flags, budgets and model policy",
    "roles.manage": "Grant and revoke console roles",
    "audit.read": "Read the staff audit",
}


@dataclass(frozen=True)
class Destination:
    key: str
    label: str
    href: str
    #: Any one of these lets a person open it.
    capabilities: tuple[str, ...]


#: The eight destinations of handoff 32, in order.
DESTINATIONS: tuple[Destination, ...] = (
    Destination("overview", "Overview", "/superadmin/overview", ("overview.read",)),
    Destination("users", "Users and access", "/superadmin/users", ("users.read",)),
    Destination("support", "Support", "/superadmin/support", ("support.read",)),
    Destination(
        "quality", "Quality and evaluations", "/superadmin/quality", ("quality.read",)
    ),
    Destination(
        "analytics", "Product analytics", "/superadmin/analytics", ("analytics.read",)
    ),
    Destination(
        "revenue", "Revenue and costs", "/superadmin/revenue", ("revenue.read",)
    ),
    Destination(
        "operations", "Operations", "/superadmin/operations", ("operations.read",)
    ),
    Destination(
        "controls",
        "Controls and audit",
        "/superadmin/controls",
        ("providers.read", "policy.read", "roles.manage", "audit.read"),
    ),
)


def tier_roles(user: UserModel) -> set[str]:
    """The roles a staff tier carries by itself."""
    tier = getattr(user, "staff_role", None)
    if tier == StaffRole.SUPERADMIN.value:
        return {OWNER}
    if tier == StaffRole.SUPPORT.value:
        return {SUPPORT}
    return set()


async def granted_roles(user_id: int) -> set[str]:
    async with db_client.async_session() as session:
        rows = await session.execute(
            select(StaffRoleGrantModel.role).where(
                StaffRoleGrantModel.user_id == user_id,
                StaffRoleGrantModel.revoked_at.is_(None),
            )
        )
        return {role for (role,) in rows.all() if role in ALL_ROLES}


async def effective_roles(user: UserModel) -> set[str]:
    """Tier roles, plus live grants while ``staff_roles`` is on. Not staff at
    all means no roles, whatever rows exist."""
    roles = tier_roles(user)
    if not roles:
        return set()
    if features.is_on("staff_roles"):
        roles |= await granted_roles(user.id)
    return roles


def capabilities_for(roles: set[str]) -> list[str]:
    return [cap for cap, holders in CAPABILITIES.items() if roles & set(holders)]


def can(roles: set[str], capability: str) -> bool:
    if capability not in CAPABILITIES:
        raise KeyError(capability)
    return bool(roles & set(CAPABILITIES[capability]))


def destinations_for(roles: set[str]) -> list[dict]:
    return [
        {
            "key": d.key,
            "label": d.label,
            "href": d.href,
            "allowed": any(can(roles, cap) for cap in d.capabilities),
        }
        for d in DESTINATIONS
    ]


def matrix() -> dict:
    return {
        "roles": list(ALL_ROLES),
        "grantable": list(GRANTABLE),
        "capabilities": [
            {
                "name": cap,
                "label": CAPABILITY_LABELS[cap],
                "roles": list(holders),
            }
            for cap, holders in CAPABILITIES.items()
        ],
    }


@dataclass
class StaffContext:
    user: UserModel
    roles: set[str]

    def can(self, capability: str) -> bool:
        return can(self.roles, capability)


def require(*capabilities: str):
    """A route dependency: the caller is staff and holds at least one of
    ``capabilities``. A 403 names the capability, never what lies behind it."""
    for cap in capabilities:
        if cap not in CAPABILITIES:
            raise KeyError(cap)

    async def _dependency(
        user: Annotated[UserModel, Depends(get_staff)],
    ) -> StaffContext:
        roles = await effective_roles(user)
        if not any(can(roles, cap) for cap in capabilities):
            raise HTTPException(
                status_code=403,
                detail=f"Your console role does not include {capabilities[0]}.",
            )
        return StaffContext(user=user, roles=roles)

    _dependency.capabilities = capabilities  # read by tests
    return _dependency
