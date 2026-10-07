"""Users, invitations and access (screens 30-31).

What staff see about a person is their access, not their content: account
state, workspaces and roles, KYC *status* (never a document), invitation
history, last useful outcome, task states and counts, daily allowances and
connection health. Task titles, briefs, messages and memory are never read
here (handoff: "without private message bodies").

Changes go through staff commands: ``invite.issue`` (from the waitlist, with
a capacity preview and per-item outcomes), ``invite.revoke``,
``user.suspend`` (a second person approves; the preview lists affected
workspaces, open tasks and armed routines), ``user.unsuspend`` and
``allowance.grant`` (the controls stream's temporary allowance).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.controls_models import AgentTaskTransitionModel
from api.db.models import (
    AgentTaskModel,
    MemberConnectionModel,
    OrganizationKycModel,
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
)
from api.db.shell_models import WaitlistRequestModel
from api.db.signup_invite_models import SignupInviteModel, SignupInviteRedemptionModel
from api.services import features
from api.services.staff import analytics, commands

OPEN_LEDGER_STATES = (
    "queued",
    "running",
    "needs_input",
    "awaiting_approval",
    "scheduled",
)


def access_state(user: UserModel) -> str:
    if user.staff_suspended_at is not None:
        return "suspended"
    return "active"


def _mask(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    local, _, domain = email.partition("@")
    return f"{local[:2]}{'•' * max(1, len(local) - 2)}@{domain}"


async def list_users(
    session: AsyncSession,
    *,
    query: str | None = None,
    state: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    q = select(UserModel).order_by(UserModel.created_at.desc()).limit(limit)
    if query:
        like = f"%{query.strip().lower()}%"
        conditions = [func.lower(UserModel.email).like(like)]
        if query.strip().isdigit():
            conditions.append(UserModel.id == int(query.strip()))
        q = q.where(or_(*conditions))
    if state == "suspended":
        q = q.where(UserModel.staff_suspended_at.isnot(None))
    elif state == "active":
        q = q.where(UserModel.staff_suspended_at.is_(None))
    users = (await session.execute(q)).scalars().all()
    ids = [u.id for u in users]
    workspaces = (
        dict(
            (
                await session.execute(
                    select(
                        OrganizationMembershipModel.user_id,
                        func.count(OrganizationMembershipModel.id),
                    )
                    .where(OrganizationMembershipModel.user_id.in_(ids))
                    .group_by(OrganizationMembershipModel.user_id)
                )
            ).all()
        )
        if ids
        else {}
    )
    kyc = await _kyc_by_user(session, ids)
    last = await analytics.last_useful_outcome(session, ids) if ids else {}
    rows = [
        {
            "id": u.id,
            "email": u.email,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "access_state": access_state(u),
            "staff": u.staff_role,
            "workspaces": int(workspaces.get(u.id, 0)),
            "kyc_status": kyc.get(u.id),
            "last_useful_outcome_at": last.get(u.id),
            "mfa_enabled": bool(u.mfa_enabled),
        }
        for u in users
    ]
    return {"users": rows, "count": len(rows), "limit": limit}


async def _kyc_by_user(session: AsyncSession, user_ids: list[int]) -> dict[int, str]:
    """The KYC status of the person's selected workspace -- the status word
    only, never a document or a field from one."""
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(UserModel.id, OrganizationKycModel.status)
            .join(
                OrganizationKycModel,
                OrganizationKycModel.organization_id
                == UserModel.selected_organization_id,
            )
            .where(UserModel.id.in_(user_ids))
        )
    ).all()
    return {uid: status for uid, status in rows}


async def waitlist(
    session: AsyncSession, *, status: str | None = "waitlisted", limit: int = 100
) -> list[dict]:
    q = (
        select(WaitlistRequestModel)
        .order_by(WaitlistRequestModel.created_at)
        .limit(limit)
    )
    if status:
        q = q.where(WaitlistRequestModel.status == status)
    return [
        {
            "id": w.id,
            "email": w.email,
            "language": w.language,
            # What the person asked Decibyl to do first: they wrote it to us
            # to be read, and the design asks for it on the review panel.
            "first_task": w.first_task,
            "occupation": w.occupation,
            "source": w.source,
            "status": w.status,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in (await session.execute(q)).scalars().all()
    ]


async def capacity(session: AsyncSession) -> dict[str, Any]:
    """The beta's capacity: configured seats against people already in and
    invitations still open. Unknown capacity is ``needs_setup``, never
    unlimited."""
    people = await session.scalar(
        select(func.count(UserModel.id)).where(UserModel.staff_role.is_(None))
    )
    now = datetime.now(UTC)
    open_invites = await session.scalar(
        select(
            func.coalesce(
                func.sum(SignupInviteModel.max_uses - SignupInviteModel.uses), 0
            )
        ).where(
            SignupInviteModel.revoked_at.is_(None),
            SignupInviteModel.uses < SignupInviteModel.max_uses,
            or_(
                SignupInviteModel.expires_at.is_(None),
                SignupInviteModel.expires_at > now,
            ),
        )
    )
    limit = constants.STAFF_PILOT_USER_CAPACITY
    used = int(people or 0) + int(open_invites or 0)
    return {
        "state": "needs_setup" if limit is None else "ok",
        "limit": limit,
        "people": int(people or 0),
        "open_invites": int(open_invites or 0),
        "remaining": None if limit is None else max(0, limit - used),
        "setting": "STAFF_PILOT_USER_CAPACITY",
    }


async def detail(session: AsyncSession, user_id: int) -> dict[str, Any] | None:
    user = await session.get(UserModel, user_id)
    if user is None:
        return None
    memberships = (
        await session.execute(
            select(OrganizationMembershipModel, OrganizationModel)
            .join(
                OrganizationModel,
                OrganizationModel.id == OrganizationMembershipModel.organization_id,
            )
            .where(OrganizationMembershipModel.user_id == user_id)
        )
    ).all()
    kyc_rows = (
        dict(
            (
                await session.execute(
                    select(
                        OrganizationKycModel.organization_id,
                        OrganizationKycModel.status,
                    ).where(
                        OrganizationKycModel.organization_id.in_(
                            [o.id for _, o in memberships]
                        )
                    )
                )
            ).all()
        )
        if memberships
        else {}
    )
    workspaces = [
        {
            "id": org.id,
            "name": org.name or f"Organization {org.id}",
            "role": m.role,
            "kind": org.kind or "workspace",
            "selected": org.id == user.selected_organization_id,
            "kyc_status": kyc_rows.get(org.id),
        }
        for m, org in memberships
    ]
    invite = (
        await session.execute(
            select(SignupInviteRedemptionModel)
            .where(SignupInviteRedemptionModel.user_id == user_id)
            .order_by(SignupInviteRedemptionModel.redeemed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    last = (await analytics.last_useful_outcome(session, [user_id])).get(user_id)
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "access_state": access_state(user),
            "suspended_at": user.staff_suspended_at.isoformat()
            if user.staff_suspended_at
            else None,
            "staff": user.staff_role,
            "mfa_enabled": bool(user.mfa_enabled),
            "email_verified": user.email_verified_at is not None,
            "last_useful_outcome_at": last,
        },
        "workspaces": workspaces,
        "invitation": {
            "invite_id": invite.invite_id,
            "door": invite.door,
            "redeemed_at": invite.redeemed_at.isoformat()
            if invite.redeemed_at
            else None,
        }
        if invite
        else None,
        # Assisted access is the existing impersonation flow, superadmin only
        # and audited there; nothing here turns it on.
        "assisted_access": "off",
    }


async def tasks(
    session: AsyncSession, user_id: int, organization_id: int | None, limit: int = 50
) -> dict:
    """The person's tasks as states and times only -- no title, brief or
    result. Scoped to one workspace the person belongs to."""
    q = (
        select(
            AgentTaskModel.id,
            AgentTaskModel.organization_id,
            AgentTaskModel.number,
            AgentTaskModel.status,
            AgentTaskModel.ledger_state,
            AgentTaskModel.assignee_workflow_id,
            AgentTaskModel.created_at,
            AgentTaskModel.finished_at,
            AgentTaskModel.outcome_evidence,
        )
        .where(AgentTaskModel.created_by == user_id)
        .order_by(AgentTaskModel.created_at.desc())
        .limit(limit)
    )
    if organization_id is not None:
        q = q.where(AgentTaskModel.organization_id == organization_id)
    rows = (await session.execute(q)).all()
    return {
        "tasks": [
            {
                "id": r.id,
                "organization_id": r.organization_id,
                "number": r.number,
                "state": r.ledger_state or r.status,
                "ledger": r.ledger_state is not None,
                "kind": "agent_task" if r.assignee_workflow_id else "person_task",
                "has_evidence": bool(r.outcome_evidence),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]
    }


async def connections(session: AsyncSession, user_id: int) -> list[dict]:
    """Connection health: which app and whether an account is attached. The
    account id itself is a provider reference and is not shown."""
    rows = (
        (
            await session.execute(
                select(MemberConnectionModel).where(
                    MemberConnectionModel.user_id == user_id
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "toolkit": r.toolkit,
            "organization_id": r.organization_id,
            "state": "connected" if r.connected_account_id else "needs_setup",
            "since": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def limits(user_id: int) -> dict:
    """Effective daily allowances and live grants, from the controls stream.
    Off, they are ``disabled_by_policy`` -- not zero and not unlimited."""
    if not features.is_on("operational_quotas"):
        return {
            "state": "disabled_by_policy",
            "reason": "operational_quotas is off",
            "allowances": [],
            "grants": [],
        }
    from api.services import quotas

    return {
        "state": "ok",
        "allowances": await quotas.status(user_id),
        "grants": await quotas.grants(user_id),
    }


# --- commands ----------------------------------------------------------------


class InviteIssueTarget(commands.Target):
    waitlist_ids: list[int] = Field(min_length=1, max_length=100)
    expires_in_days: int = Field(default=14, ge=1, le=30)


async def _invite_eligible(session: AsyncSession, t: InviteIssueTarget) -> str | None:
    cap = await capacity(session)
    if cap["remaining"] is not None and len(set(t.waitlist_ids)) > cap["remaining"]:
        return (
            f"Capacity exhausted: {cap['remaining']} place(s) left for "
            f"{len(set(t.waitlist_ids))} invitation(s)."
        )
    return None


async def _invite_preview(session: AsyncSession, t: InviteIssueTarget) -> dict:
    rows = (
        (
            await session.execute(
                select(WaitlistRequestModel).where(
                    WaitlistRequestModel.id.in_(t.waitlist_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    found = {r.id: r for r in rows}
    return {
        "cohort": [
            {
                "waitlist_id": wid,
                "email": _mask(found[wid].email) if wid in found else None,
                "eligible": wid in found and found[wid].status == "waitlisted",
            }
            for wid in t.waitlist_ids
        ],
        "capacity": await capacity(session),
        "expires_in_days": t.expires_in_days,
        "delivery": "Codes are issued bound to each address; Decibyl does not email them yet.",
    }


def signup_invites_email(email: str) -> str:
    return (email or "").strip().lower()


async def _fresh_code(session: AsyncSession, signup_invites) -> str:
    while True:
        code = signup_invites.generate_code()
        taken = await session.scalar(
            select(SignupInviteModel.id).where(SignupInviteModel.code == code)
        )
        if taken is None:
            return code


async def _invite_issue(
    session: AsyncSession, t: InviteIssueTarget, actor: commands.Actor
) -> commands.Outcome:
    from api.services.auth import signup_invites

    now = datetime.now(UTC)
    items = []
    for wid in dict.fromkeys(t.waitlist_ids):
        row = await session.get(WaitlistRequestModel, wid, with_for_update=True)
        if row is None:
            items.append({"waitlist_id": wid, "outcome": "not_found"})
            continue
        if row.status != "waitlisted":
            items.append({"waitlist_id": wid, "outcome": f"already_{row.status}"})
            continue
        # Minted in this command's transaction (not ``signup_invites.mint``,
        # which commits its own), so a code exists exactly when its waitlist
        # row says invited.
        invite = SignupInviteModel(
            code=await _fresh_code(session, signup_invites),
            email=signup_invites_email(row.email),
            max_uses=1,
            uses=0,
            note=f"waitlist #{wid} (staff command #{actor.command_id})",
            created_by_user_id=actor.approved_by or actor.requested_by,
            expires_at=now + timedelta(days=t.expires_in_days),
        )
        session.add(invite)
        await session.flush()
        minted = [invite]
        row.status = "invited"
        row.updated_at = now
        items.append(
            {
                "waitlist_id": wid,
                "outcome": "issued",
                "invite_id": minted[0].id,
                "delivery": "not_sent",
            }
        )
    issued = sum(1 for i in items if i["outcome"] == "issued")
    state = (
        commands.SUCCEEDED
        if issued == len(items)
        else (commands.FAILED if issued == 0 else commands.SUCCEEDED)
    )
    return commands.Outcome(
        {"items": items, "issued": issued, "partial": 0 < issued < len(items)},
        state=state,
        reason_code=None if issued else "nothing_issued",
    )


class InviteRevokeTarget(commands.Target):
    invite_id: int = Field(gt=0)


async def _invite_revoke_eligible(
    session: AsyncSession, t: InviteRevokeTarget
) -> str | None:
    invite = await session.get(SignupInviteModel, t.invite_id)
    if invite is None:
        return "There is no such invitation."
    if invite.revoked_at is not None:
        return "This invitation is already revoked."
    if invite.uses >= invite.max_uses:
        return "This invitation was already accepted; suspend the account instead."
    return None


async def _invite_revoke(
    session: AsyncSession, t: InviteRevokeTarget, actor: commands.Actor
) -> commands.Outcome:
    invite = await session.get(SignupInviteModel, t.invite_id, with_for_update=True)
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(UTC)
    return commands.Outcome({"invite_id": invite.id, "message": "Revoked."})


class UserTarget(commands.Target):
    user_id: int = Field(gt=0)


async def _suspend_eligible(session: AsyncSession, t: UserTarget) -> str | None:
    user = await session.get(UserModel, t.user_id)
    if user is None:
        return "There is no such user."
    if user.staff_role:
        return "Staff accounts are not suspended from the console."
    if user.staff_suspended_at is not None:
        return "This account is already suspended."
    return None


async def _suspend_preview(session: AsyncSession, t: UserTarget) -> dict:
    from api.db.models import AgentRoutineModel

    orgs = (
        (
            await session.execute(
                select(OrganizationMembershipModel.organization_id).where(
                    OrganizationMembershipModel.user_id == t.user_id
                )
            )
        )
        .scalars()
        .all()
    )
    open_tasks = await session.scalar(
        select(func.count(AgentTaskModel.id)).where(
            AgentTaskModel.created_by == t.user_id,
            or_(
                AgentTaskModel.ledger_state.in_(OPEN_LEDGER_STATES),
                (AgentTaskModel.ledger_state.is_(None))
                & AgentTaskModel.status.in_(("todo", "doing", "waiting")),
            ),
        )
    )
    # Routines have no author column; the ones that are certainly this
    # person's are those armed in their personal space.
    routines = await session.scalar(
        select(func.count(AgentRoutineModel.id))
        .join(
            OrganizationModel, OrganizationModel.id == AgentRoutineModel.organization_id
        )
        .where(
            OrganizationModel.personal_owner_user_id == t.user_id,
            AgentRoutineModel.is_active.is_(True),
        )
    )
    return {
        "workspaces": len(orgs),
        "open_tasks": int(open_tasks or 0),
        "personal_routines_armed": int(routines or 0),
        "effect": (
            "Every request this person makes is refused from now on. Work "
            "already running is not stopped, and the workspaces they belong "
            "to carry on for their other members."
        ),
    }


async def _suspend(
    session: AsyncSession, t: UserTarget, actor: commands.Actor
) -> commands.Outcome:
    user = await session.get(UserModel, t.user_id, with_for_update=True)
    if user.staff_suspended_at is None:
        user.staff_suspended_at = datetime.now(UTC)
    return commands.Outcome(
        {"user_id": user.id, "suspended_at": user.staff_suspended_at.isoformat()}
    )


async def _unsuspend_eligible(session: AsyncSession, t: UserTarget) -> str | None:
    user = await session.get(UserModel, t.user_id)
    if user is None:
        return "There is no such user."
    if user.staff_suspended_at is None:
        return "This account is not suspended."
    return None


async def _unsuspend(
    session: AsyncSession, t: UserTarget, actor: commands.Actor
) -> commands.Outcome:
    user = await session.get(UserModel, t.user_id, with_for_update=True)
    user.staff_suspended_at = None
    return commands.Outcome({"user_id": user.id, "message": "Access restored."})


class AllowanceTarget(commands.Target):
    user_id: int = Field(gt=0)
    kind: Literal[
        "model_turns", "voice_minutes", "outbound_messages", "browser_minutes"
    ]
    extra: int = Field(gt=0, le=10_000)
    hours: int = Field(gt=0, le=31 * 24)


async def _allowance_eligible(session: AsyncSession, t: AllowanceTarget) -> str | None:
    if not features.is_on("operational_quotas"):
        return (
            "Daily limits are off (operational_quotas), so there is nothing to raise."
        )
    if await session.get(UserModel, t.user_id) is None:
        return "There is no such user."
    return None


async def _allowance_preview(session: AsyncSession, t: AllowanceTarget) -> dict:
    return {
        "user_id": t.user_id,
        "kind": t.kind,
        "extra_per_day": t.extra,
        "expires_at": (datetime.now(UTC) + timedelta(hours=t.hours)).isoformat(),
        "scope": "This person only; the platform budget is unchanged.",
    }


async def _allowance(
    session: AsyncSession, t: AllowanceTarget, actor: commands.Actor
) -> commands.Outcome:
    from api.services import quotas

    command = await session.get(commands.StaffCommandModel, actor.command_id)
    try:
        row = await quotas.grant(
            user_id=t.user_id,
            kind=t.kind,
            extra=t.extra,
            reason=command.reason,
            expires_at=datetime.now(UTC) + timedelta(hours=t.hours),
            granted_by=actor.approved_by or actor.requested_by,
        )
    except (quotas.GrantRefused, ValueError) as exc:
        return commands.Outcome(
            {"message": str(exc)}, state=commands.FAILED, reason_code="grant_refused"
        )
    return commands.Outcome(
        {"allowance_id": row.id, "expires_at": row.expires_at.isoformat()}
    )


commands.register(
    commands.CommandSpec(
        name="invite.issue",
        summary="Invite people from the waitlist; one code per address.",
        request_capability="users.invite",
        target=InviteIssueTarget,
        handler=_invite_issue,
        eligible=_invite_eligible,
        preview=_invite_preview,
    )
)
commands.register(
    commands.CommandSpec(
        name="invite.revoke",
        summary="Revoke an invitation that has not been accepted.",
        request_capability="users.invite",
        target=InviteRevokeTarget,
        handler=_invite_revoke,
        eligible=_invite_revoke_eligible,
    )
)
commands.register(
    commands.CommandSpec(
        name="user.suspend",
        summary="Suspend an account; a second person approves.",
        request_capability="users.suspend.request",
        approve_capability="users.suspend.approve",
        target=UserTarget,
        handler=_suspend,
        eligible=_suspend_eligible,
        preview=_suspend_preview,
    )
)
commands.register(
    commands.CommandSpec(
        name="user.unsuspend",
        summary="Restore a suspended account.",
        request_capability="users.suspend.request",
        target=UserTarget,
        handler=_unsuspend,
        eligible=_unsuspend_eligible,
    )
)
commands.register(
    commands.CommandSpec(
        name="allowance.grant",
        summary="A temporary extra daily allowance for one person.",
        request_capability="users.allowance",
        target=AllowanceTarget,
        handler=_allowance,
        eligible=_allowance_eligible,
        preview=_allowance_preview,
        notes=(
            "Support cannot raise the platform budget; this is one person, with an expiry.",
        ),
    )
)


async def transitions_for(
    session: AsyncSession, task_id: int, organization_id: int
) -> list[dict]:
    rows = (
        (
            await session.execute(
                select(AgentTaskTransitionModel)
                .where(
                    AgentTaskTransitionModel.task_id == task_id,
                    AgentTaskTransitionModel.organization_id == organization_id,
                )
                .order_by(AgentTaskTransitionModel.sequence)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "sequence": r.sequence,
            "from": r.from_state,
            "to": r.to_state,
            "reason_code": r.reason_code,
            "at": r.occurred_at.isoformat() if r.occurred_at else None,
        }
        for r in rows
    ]
