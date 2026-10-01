"""Is this account healthy? The facts the staff accounts screens add (ADMIN-2).

The accounts list answered "how much is it spending"; staff running trial
customers without SSH also need "what plan is it on, when does the trial end,
has it built anything, which apps has it linked, can it get a number, is it on
its own keys, and what broke lately". Each answer lives in a different table.

Every query here takes the *set* of organisation ids it is asked about and
aggregates with ``GROUP BY`` -- one round trip per fact for the whole list,
never one per account -- and every query filters by those ids, so asking about
one account can only ever return that account's rows.

Read-only. Nothing here changes an account; the trial controls on the
drill-down go through ``POST /superuser/organizations/{id}/trial``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.channel_identity_models import ChannelIdentityModel
from api.db.models import (
    AgentEventModel,
    OrganizationKycModel,
    OrganizationModel,
    OrganizationProviderCredentialModel,
    PaymentMandateModel,
    WorkflowModel,
)
from api.enums import AgentEventKind, KycStatus, MandateStatus, WorkflowStatus

#: Mirrors ``mandates.PURPOSE_STARTER_PLAN`` without importing the service
#: layer into a DB client (the same convention as ``kpi_board_client``).
PLAN_PURPOSE = "starter_plan"

#: What a plan-less account is on when the trial does not apply to it.
FREE = "free"
TRIAL = "trial"

#: How many recent failures the drill-down shows.
RECENT_FAILURES_LIMIT = 5

#: The four apps an account can link today (DCH-1). Listed so the screen shows
#: a zero for an app nobody linked rather than leaving it out; a channel not in
#: this tuple still appears, under its own name, because it is counted from
#: the rows rather than from this list.
KNOWN_CHANNELS = ("whatsapp", "telegram", "slack", "teams")


def _ids(organization_ids: Iterable[int]) -> list[int]:
    return sorted({int(i) for i in organization_ids})


async def _plans(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    rows = await session.execute(
        select(
            PaymentMandateModel.organization_id,
            func.max(PaymentMandateModel.plan_code),
        )
        .where(
            PaymentMandateModel.organization_id.in_(ids),
            PaymentMandateModel.purpose == PLAN_PURPOSE,
            PaymentMandateModel.status.in_(MandateStatus.authorised()),
        )
        .group_by(PaymentMandateModel.organization_id)
    )
    return {int(org): code for org, code in rows.all() if code}


async def _agents(session: AsyncSession, ids: list[int]) -> dict[int, tuple[int, int]]:
    active = WorkflowModel.status == WorkflowStatus.ACTIVE.value
    rows = await session.execute(
        select(
            WorkflowModel.organization_id,
            func.count().filter(active),
            func.count().filter(and_(active, WorkflowModel.is_live.is_(True))),
        )
        .where(WorkflowModel.organization_id.in_(ids))
        .group_by(WorkflowModel.organization_id)
    )
    return {int(org): (int(total), int(live)) for org, total, live in rows.all()}


async def _channels(session: AsyncSession, ids: list[int]) -> dict[int, dict[str, int]]:
    rows = await session.execute(
        select(
            ChannelIdentityModel.organization_id,
            ChannelIdentityModel.channel,
            func.count(),
        )
        .where(ChannelIdentityModel.organization_id.in_(ids))
        .group_by(ChannelIdentityModel.organization_id, ChannelIdentityModel.channel)
    )
    out: dict[int, dict[str, int]] = {}
    for org, channel, count in rows.all():
        out.setdefault(int(org), {})[str(channel)] = int(count)
    return out


async def _kyc(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    rows = await session.execute(
        select(OrganizationKycModel.organization_id, OrganizationKycModel.status).where(
            OrganizationKycModel.organization_id.in_(ids)
        )
    )
    return {int(org): str(status) for org, status in rows.all()}


async def _byok(session: AsyncSession, ids: list[int]) -> dict[int, list[str]]:
    cred = OrganizationProviderCredentialModel
    rows = await session.execute(
        select(cred.organization_id, cred.provider)
        .where(cred.organization_id.in_(ids), cred.is_active.is_(True))
        .group_by(cred.organization_id, cred.provider)
    )
    out: dict[int, list[str]] = {}
    for org, provider in rows.all():
        out.setdefault(int(org), []).append(str(provider))
    return {org: sorted(providers) for org, providers in out.items()}


def _trial_view(
    *,
    organization_id: int,
    created_at: datetime | None,
    override_ends_at: datetime | None,
    has_plan: bool,
) -> dict:
    """Where the account stands on the trial, computed from columns already
    read -- ``trial.status`` would cost two queries per account.

    Mirrors ``trial.status``: on the trial only while the ``trial_plan`` flag
    is on for the account and it has no authorised plan mandate.
    """
    from api.services.billing import trial

    override = override_ends_at is not None
    if has_plan or not trial.applies(organization_id):
        return {
            "on_trial": False,
            "active": False,
            "stage": "not_on_trial",
            "notice_stage": None,
            "starts_at": None,
            "ends_at": None,
            "days_left": None,
            "override": override,
        }
    starts, ends = trial.window(
        created_at=created_at, override_ends_at=override_ends_at
    )
    status_ = trial.TrialStatus(
        on_trial=True,
        active=datetime.now(UTC) < ends,
        starts_at=starts,
        ends_at=ends,
    )
    days_left = status_.days_left
    if not status_.active:
        stage = "ended"
    elif days_left is not None and days_left <= max(trial.NOTICE_STAGES):
        stage = "ending_soon"
    else:
        stage = "active"
    return {
        "on_trial": True,
        "active": status_.active,
        "stage": stage,
        # The notice the daily job would send today, if any (3_days, 1_day,
        # ended) -- so staff can see what the customer was just told.
        "notice_stage": trial.stage_for(status_),
        "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat(),
        "days_left": days_left,
        "override": override,
    }


async def health_for(
    session: AsyncSession, organization_ids: Iterable[int]
) -> dict[int, dict]:
    """The Org 360 columns for each of ``organization_ids``.

    Six grouped queries for any number of accounts. An id with no
    organisation row is absent from the result.
    """
    ids = _ids(organization_ids)
    if not ids:
        return {}

    orgs = (
        await session.execute(
            select(
                OrganizationModel.id,
                OrganizationModel.created_at,
                OrganizationModel.trial_ends_at,
            ).where(OrganizationModel.id.in_(ids))
        )
    ).all()
    plans = await _plans(session, ids)
    agents = await _agents(session, ids)
    channels = await _channels(session, ids)
    kyc = await _kyc(session, ids)
    byok = await _byok(session, ids)

    from api.services.billing import trial

    out: dict[int, dict] = {}
    for org_id, created_at, trial_ends_at in orgs:
        org_id = int(org_id)
        plan_code = plans.get(org_id)
        if plan_code is None:
            plan_code = TRIAL if trial.applies(org_id) else FREE
        linked = {name: 0 for name in KNOWN_CHANNELS}
        linked.update(channels.get(org_id, {}))
        total_agents, live_agents = agents.get(org_id, (0, 0))
        providers = byok.get(org_id, [])
        out[org_id] = {
            "plan": plan_code,
            "plan_is_paid": org_id in plans,
            "trial_ends_at": trial_ends_at.isoformat() if trial_ends_at else None,
            "trial": _trial_view(
                organization_id=org_id,
                created_at=created_at,
                override_ends_at=trial_ends_at,
                has_plan=org_id in plans,
            ),
            "agents_count": total_agents,
            "live_agents_count": live_agents,
            "channels": linked,
            "channels_linked": sum(linked.values()),
            "kyc_status": kyc.get(org_id, KycStatus.NOT_STARTED.value),
            "byok_keys_present": bool(providers),
            "byok_providers": providers,
        }
    return out


async def recent_failures(
    session: AsyncSession,
    *,
    organization_id: int,
    limit: int = RECENT_FAILURES_LIMIT,
) -> list[dict]:
    """The last few times an agent in this account could not do its job.

    Read from the agent timeline's ``could_not`` events -- the one place every
    path (routines, triggers, channel replies, the assistant, filing) records
    a failure with the sentence a person reads -- rather than guessed from
    run columns, which carry no failure state of their own.
    """
    rows = await session.execute(
        select(
            AgentEventModel.id,
            AgentEventModel.at,
            AgentEventModel.summary,
            AgentEventModel.workflow_id,
            AgentEventModel.workflow_run_id,
            WorkflowModel.name,
        )
        .outerjoin(WorkflowModel, WorkflowModel.id == AgentEventModel.workflow_id)
        .where(
            AgentEventModel.organization_id == organization_id,
            AgentEventModel.kind == AgentEventKind.COULD_NOT.value,
        )
        .order_by(AgentEventModel.at.desc(), AgentEventModel.id.desc())
        .limit(limit)
    )
    return [
        {
            "id": int(r.id),
            "at": r.at.isoformat() if r.at else None,
            "summary": r.summary,
            "workflow_id": r.workflow_id,
            "workflow_name": r.name,
            "workflow_run_id": r.workflow_run_id,
        }
        for r in rows.all()
    ]
