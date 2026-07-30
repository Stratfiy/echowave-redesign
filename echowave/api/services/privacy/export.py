"""Handing back everything we hold about someone.

GDPR Art 20 (portability) wants a structured, commonly used, machine-readable
format. DPDP s11 (right to access) wants a summary of what is processed and who
it has been shared with. JSON satisfies both, and the same function answers
both questions because they are the same question asked by two statutes.

Two scopes, because two different people ask:

* **A data principal** — usually the person who answered the phone — asks what
  is held about *them*. Scoped to one number within one account.
* **A customer** asks for everything in their own account, generally because
  they are leaving and want their data.

Sub-processors are listed in the export rather than left to a policy page. DPDP
s11(1)(c) asks who data has been shared with, and for a voice platform the true
answer is "whichever speech and language vendors ran this call" — which is
recorded per call, so it can be answered exactly rather than generically.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    BillingProfileModel,
    CallCostItemModel,
    OrganizationModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.privacy.erasure import find_runs_for_number, normalise_number


async def _subprocessors_for_run(
    session: AsyncSession, *, workflow_run_id: int
) -> list[str]:
    """Which vendors actually handled this call.

    Read from the cost items, which record the provider behind every priced
    line. That makes the answer specific to the call rather than a list of
    everyone we have ever integrated.
    """
    rows = (
        await session.scalars(
            select(CallCostItemModel.provider).where(
                CallCostItemModel.workflow_run_id == workflow_run_id
            )
        )
    ).all()
    return sorted({p for p in rows if p})


def _run_view(run: WorkflowRunModel) -> dict:
    from api.services.privacy.retention import PURGED_MARKER

    return {
        "call_id": run.id,
        "started_at": run.created_at.isoformat() if run.created_at else None,
        "ended_at": run.ended_at.isoformat()
        if getattr(run, "ended_at", None)
        else None,
        "direction": run.call_type,
        "channel": run.mode,
        "duration_seconds": run.billable_seconds,
        # Deliberately reports the state rather than omitting the field: "we
        # deleted this" and "we never had this" are different answers, and a
        # person exercising a right is entitled to the accurate one.
        "recording": (
            "erased" if run.recording_url == PURGED_MARKER else bool(run.recording_url)
        ),
        "transcript": (
            "erased"
            if run.transcript_url == PURGED_MARKER
            else bool(run.transcript_url)
        ),
        "context_collected": run.gathered_context or {},
        "initial_context": run.initial_context or {},
    }


async def export_data_principal(
    session: AsyncSession, *, organization_id: int, number: str
) -> dict:
    """Everything one account holds about one phone number."""
    runs = await find_runs_for_number(
        session, organization_id=organization_id, number=number
    )

    calls = []
    for run in runs:
        view = _run_view(run)
        view["shared_with"] = await _subprocessors_for_run(
            session, workflow_run_id=run.id
        )
        calls.append(view)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "data_principal",
        # Echoed in the normalised form actually matched on, so the requester
        # can see what was searched for rather than guessing.
        "subject": normalise_number(number),
        "calls": calls,
        "call_count": len(calls),
        "notes": [
            "Duration and cost are retained for statutory accounting periods "
            "even after a call's content is erased.",
            "'shared_with' lists the processors that handled each call.",
        ],
    }


async def export_organization(
    session: AsyncSession, *, organization_id: int, limit: int = 5000
) -> dict:
    """Everything an account holds, for a customer taking their data elsewhere.

    Bounded, because an unbounded export of a busy account is a request that
    never returns. The bound is reported in the output so a truncated export is
    never mistaken for a complete one.
    """
    organization = await session.get(OrganizationModel, organization_id)
    profile = await session.scalar(
        select(BillingProfileModel).where(
            BillingProfileModel.organization_id == organization_id
        )
    )

    runs = (
        await session.scalars(
            select(WorkflowRunModel)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(WorkflowModel.organization_id == organization_id)
            .order_by(WorkflowRunModel.created_at.desc())
            .limit(limit)
        )
    ).all()

    total = await session.scalar(
        select(WorkflowRunModel.id)
        .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
        .where(WorkflowModel.organization_id == organization_id)
        .order_by(WorkflowRunModel.id.desc())
        .limit(1)
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "organization",
        "account": {
            "id": organization_id,
            "created_at": (
                organization.created_at.isoformat()
                if organization and organization.created_at
                else None
            ),
            "billing_profile": (
                {
                    "legal_name": profile.legal_name,
                    "gstin": profile.gstin,
                    "address_line1": profile.address_line1,
                    "address_line2": profile.address_line2,
                    "city": profile.city,
                    "state_code": profile.state_code,
                    "postal_code": profile.postal_code,
                    "country_code": profile.country_code,
                }
                if profile
                else None
            ),
        },
        "calls": [_run_view(run) for run in runs],
        "call_count": len(runs),
        "truncated": len(runs) >= limit,
        "limit": limit,
        "highest_call_id": total,
    }
