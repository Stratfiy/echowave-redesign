"""Workspaces from the staff console (phase 3, `staff`).

Two things the console lacked: a workspace-level view on the person screen
(plan, credit, usage, flags, recent failures -- STAFF.md "users and access"),
and suspending a whole workspace rather than one person.

**Suspension.** ``workspace.suspend`` is a two-person command like
``user.suspend``. While a workspace is suspended (and ``staff_console`` is
on):

* its members are refused while it is their selected workspace
  (``depends._refuse_if_suspended``); staff never are;
* no new run starts in it (``quota_service`` answers ``workspace_suspended``),
  which covers calls, campaigns, routines and chat runs alike. Work already
  running finishes.

The set of suspended workspace ids is cached in each process for
``CACHE_SECONDS`` so the check on every request costs no query; the command
clears this process's cache, and other processes see it within the TTL.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    AgentEventModel,
    AgentTaskModel,
    CreditLedgerModel,
    OrganizationMembershipModel,
    OrganizationModel,
)
from api.services import features
from api.services.staff import commands

CACHE_SECONDS = 30.0
SUSPENDED_DETAIL = (
    "This workspace is suspended. Write to support if you think this is a mistake."
)
RECENT_ERRORS = 5

_cache: tuple[float, frozenset[int]] | None = None


def invalidate() -> None:
    global _cache
    _cache = None


async def suspended_ids() -> frozenset[int]:
    """Every suspended workspace id, cached briefly. Fails open (an empty
    set) when the database cannot answer: a blip must not lock out every
    customer."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    from api.db import db_client

    try:
        async with db_client.async_session() as session:
            ids = frozenset(
                (
                    await session.scalars(
                        select(OrganizationModel.id).where(
                            OrganizationModel.staff_suspended_at.is_not(None)
                        )
                    )
                ).all()
            )
    except Exception:
        from loguru import logger

        logger.exception("Could not read suspended workspaces; not enforcing")
        return frozenset()
    _cache = (now, ids)
    return ids


async def is_suspended(organization_id: int | None) -> bool:
    if organization_id is None or not features.is_on("staff_console"):
        return False
    return int(organization_id) in await suspended_ids()


# --- the person screen's workspace summary ----------------------------------------


async def summaries(session: AsyncSession, org_ids: list[int]) -> dict[int, dict]:
    """Plan, credit, 28-day spend and failures, flag overrides and the last
    few failures for each workspace. Times and ids only: a failure's sentence
    can quote the customer, and the console never shows customer content."""
    if not org_ids:
        return {}
    from api.db import org_health_client
    from api.services import feature_admin

    health = await org_health_client.health_for(session, org_ids)
    since = datetime.now(UTC) - timedelta(days=28)
    balances = dict(
        (
            await session.execute(
                select(
                    CreditLedgerModel.organization_id,
                    func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0),
                )
                .where(CreditLedgerModel.organization_id.in_(org_ids))
                .group_by(CreditLedgerModel.organization_id)
            )
        ).all()
    )
    spent = dict(
        (
            await session.execute(
                select(
                    CreditLedgerModel.organization_id,
                    func.coalesce(func.sum(-CreditLedgerModel.delta_paise), 0),
                )
                .where(
                    CreditLedgerModel.organization_id.in_(org_ids),
                    CreditLedgerModel.delta_paise < 0,
                    CreditLedgerModel.created_at >= since,
                )
                .group_by(CreditLedgerModel.organization_id)
            )
        ).all()
    )
    failed_tasks = dict(
        (
            await session.execute(
                select(AgentTaskModel.organization_id, func.count(AgentTaskModel.id))
                .where(
                    AgentTaskModel.organization_id.in_(org_ids),
                    AgentTaskModel.ledger_state.in_(("failed", "unknown")),
                    AgentTaskModel.created_at >= since,
                )
                .group_by(AgentTaskModel.organization_id)
            )
        ).all()
    )
    suspended = dict(
        (
            await session.execute(
                select(OrganizationModel.id, OrganizationModel.staff_suspended_at).where(
                    OrganizationModel.id.in_(org_ids)
                )
            )
        ).all()
    )
    out: dict[int, dict] = {}
    for org_id in org_ids:
        errors = (
            await session.execute(
                select(
                    AgentEventModel.id,
                    AgentEventModel.at,
                    AgentEventModel.workflow_id,
                    AgentEventModel.workflow_run_id,
                )
                .where(
                    AgentEventModel.organization_id == org_id,
                    AgentEventModel.kind == "could_not",
                )
                .order_by(AgentEventModel.at.desc(), AgentEventModel.id.desc())
                .limit(RECENT_ERRORS)
            )
        ).all()
        try:
            flags = await feature_admin.flags_for_organization(session, org_id)
        except LookupError:
            flags = []
        h = health.get(org_id, {})
        out[org_id] = {
            "plan": h.get("plan"),
            "plan_is_paid": h.get("plan_is_paid"),
            "trial_ends_at": h.get("trial_ends_at"),
            "balance_paise": int(balances.get(org_id, 0) or 0),
            "spent_paise_28d": int(spent.get(org_id, 0) or 0),
            "failed_tasks_28d": int(failed_tasks.get(org_id, 0) or 0),
            "agents": h.get("agents_count"),
            "channels_linked": h.get("channels_linked"),
            # Only the flags this workspace sees differently from everyone,
            # or that a console row decides: the full list is 100+ long.
            "flags": [
                {
                    "name": f["name"],
                    "enabled": f["enabled"],
                    "source": "override" if f.get("override") else "environment",
                }
                for f in flags
                if f.get("override")
                or f.get("environment_listed")
                or f["enabled"] != f["global_enabled"]
            ],
            "recent_errors": [
                {
                    "id": int(r.id),
                    "at": r.at.isoformat() if r.at else None,
                    "workflow_id": r.workflow_id,
                    "workflow_run_id": r.workflow_run_id,
                }
                for r in errors
            ],
            "suspended_at": suspended[org_id].isoformat()
            if suspended.get(org_id)
            else None,
        }
    return out


# --- commands ---------------------------------------------------------------------


class WorkspaceTarget(commands.Target):
    organization_id: int = Field(gt=0)


async def _members(session: AsyncSession, organization_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count(OrganizationMembershipModel.id)).where(
                OrganizationMembershipModel.organization_id == organization_id
            )
        )
        or 0
    )


async def _suspend_eligible(session: AsyncSession, t: WorkspaceTarget) -> str | None:
    org = await session.get(OrganizationModel, t.organization_id)
    if org is None:
        return "There is no such workspace."
    if org.staff_suspended_at is not None:
        return "This workspace is already suspended."
    return None


async def _suspend_preview(session: AsyncSession, t: WorkspaceTarget) -> dict[str, Any]:
    return {
        "members": await _members(session, t.organization_id),
        "effect": (
            "Every member is refused while this is their selected workspace, "
            "and no new call, campaign, routine or chat run starts in it. "
            "Work already running finishes. Staff are not affected."
        ),
    }


async def _suspend(
    session: AsyncSession, t: WorkspaceTarget, actor: commands.Actor
) -> commands.Outcome:
    org = await session.get(OrganizationModel, t.organization_id, with_for_update=True)
    if org.staff_suspended_at is None:
        org.staff_suspended_at = datetime.now(UTC)
    invalidate()
    return commands.Outcome(
        {
            "organization_id": org.id,
            "suspended_at": org.staff_suspended_at.isoformat(),
        }
    )


async def _unsuspend_eligible(session: AsyncSession, t: WorkspaceTarget) -> str | None:
    org = await session.get(OrganizationModel, t.organization_id)
    if org is None:
        return "There is no such workspace."
    if org.staff_suspended_at is None:
        return "This workspace is not suspended."
    return None


async def _unsuspend(
    session: AsyncSession, t: WorkspaceTarget, actor: commands.Actor
) -> commands.Outcome:
    org = await session.get(OrganizationModel, t.organization_id, with_for_update=True)
    org.staff_suspended_at = None
    invalidate()
    return commands.Outcome({"organization_id": org.id, "message": "Workspace restored."})


commands.register(
    commands.CommandSpec(
        name="workspace.suspend",
        summary="Suspend a whole workspace; a second person approves.",
        request_capability="users.suspend.request",
        approve_capability="users.suspend.approve",
        target=WorkspaceTarget,
        handler=_suspend,
        eligible=_suspend_eligible,
        preview=_suspend_preview,
    )
)
commands.register(
    commands.CommandSpec(
        name="workspace.unsuspend",
        summary="Restore a suspended workspace.",
        request_capability="users.suspend.request",
        target=WorkspaceTarget,
        handler=_unsuspend,
        eligible=_unsuspend_eligible,
    )
)
