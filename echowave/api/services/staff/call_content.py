"""Call content for staff, under the customer's consent (phase 3, `staff`).

Staff could open any call's recording from the console with nothing asked of
the customer and no transcript at all. Now:

* call metadata, cost and latency stay visible to superadmins as before;
* the **transcript and the recording** open only while a workspace owner or
  admin has granted it -- for that call, or for every call in the workspace
  -- and the grant has not expired or been revoked
  (``StaffContentGrantModel``);
* each read under a grant writes ``data_access_log`` (actor ``staff``) and
  an ``admin_action_log`` row, so "who read my call" has an answer.

The customer makes and revokes grants (``/organizations/staff-access``);
staff cannot make one for them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AdminActionLogModel, WorkflowModel, WorkflowRunModel
from api.db.staff_models import StaffContentGrantModel

MAX_DAYS = 30
CONSENT_REQUIRED = (
    "The workspace has not allowed Decibyl staff to read this call. An owner "
    "or admin can allow it from the call's page in their account."
)


class GrantRefused(ValueError):
    pass


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _view(row: StaffContentGrantModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "workflow_run_id": row.workflow_run_id,
        "scope": "call" if row.workflow_run_id else "all_calls",
        "reason": row.reason,
        "granted_by_user_id": row.granted_by_user_id,
        "granted_at": _iso(row.granted_at),
        "expires_at": _iso(row.expires_at),
        "revoked_at": _iso(row.revoked_at),
    }


async def run_organization(session: AsyncSession, workflow_run_id: int) -> int | None:
    return await session.scalar(
        select(WorkflowModel.organization_id)
        .join(WorkflowRunModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
        .where(WorkflowRunModel.id == workflow_run_id)
    )


async def active_grant(
    session: AsyncSession,
    *,
    organization_id: int,
    workflow_run_id: int,
    now: datetime | None = None,
) -> StaffContentGrantModel | None:
    now = now or datetime.now(UTC)
    g = StaffContentGrantModel
    return (
        await session.scalars(
            select(g)
            .where(
                g.organization_id == organization_id,
                or_(g.workflow_run_id == workflow_run_id, g.workflow_run_id.is_(None)),
                g.revoked_at.is_(None),
                g.expires_at > now,
            )
            .order_by(g.expires_at.desc())
            .limit(1)
        )
    ).first()


async def access_state(session: AsyncSession, workflow_run_id: int) -> dict[str, Any]:
    org_id = await run_organization(session, workflow_run_id)
    if org_id is None:
        return {"state": "consent_required", "grant": None, "detail": CONSENT_REQUIRED}
    grant = await active_grant(
        session, organization_id=org_id, workflow_run_id=workflow_run_id
    )
    if grant is None:
        return {"state": "consent_required", "grant": None, "detail": CONSENT_REQUIRED}
    return {"state": "granted", "grant": _view(grant), "detail": None}


def turns_from_logs(logs: dict | None) -> list[dict[str, Any]]:
    """The conversation as turns, from the run's realtime events: final user
    transcriptions and the agent's words, in order."""
    from pipecat.utils.enums import RealtimeFeedbackType

    events = list((logs or {}).get("realtime_feedback_events") or [])
    turns: list[dict[str, Any]] = []
    for event in events:
        kind = event.get("type")
        payload = event.get("payload") or {}
        if (
            kind == RealtimeFeedbackType.USER_TRANSCRIPTION.value
            and payload.get("final") is True
        ):
            role = "caller"
        elif kind == RealtimeFeedbackType.BOT_TEXT.value:
            role = "agent"
        else:
            continue
        turns.append(
            {
                "role": role,
                "text": str(payload.get("text") or ""),
                "at": payload.get("timestamp") or event.get("timestamp"),
            }
        )
    return turns


async def read_transcript(
    session: AsyncSession,
    *,
    workflow_run_id: int,
    staff_user_id: int,
    ip_address: str | None,
) -> dict[str, Any]:
    """The transcript, if a grant covers it; ``PermissionError`` otherwise.
    Writes the access rows before returning anything."""
    from api.services.privacy import access_log

    run = await session.get(WorkflowRunModel, workflow_run_id)
    if run is None:
        raise LookupError("Call not found")
    org_id = await run_organization(session, workflow_run_id)
    grant = (
        await active_grant(session, organization_id=org_id, workflow_run_id=run.id)
        if org_id is not None
        else None
    )
    if grant is None:
        raise PermissionError(CONSENT_REQUIRED)
    await access_log.record_access(
        session,
        organization_id=org_id,
        user_id=staff_user_id,
        resource_type=access_log.TRANSCRIPT,
        resource_id=str(run.id),
        workflow_run_id=run.id,
        action="view",
        actor_kind="staff",
        ip_address=ip_address,
    )
    session.add(
        AdminActionLogModel(
            actor_user_id=staff_user_id,
            action="call_transcript_viewed",
            target_organization_id=org_id,
            actor_ip=ip_address,
            note=f"call #{run.id} under grant #{grant.id}",
        )
    )
    # Read everything before the commit: a production session expires its
    # objects on commit, and touching one afterwards is an IO error.
    turns = turns_from_logs(run.logs)
    answer = {
        "workflow_run_id": run.id,
        "grant": _view(grant),
        "turns": turns,
        "state": "ok" if turns else "empty",
    }
    await session.commit()
    return answer


async def record_recording_access(
    session: AsyncSession,
    *,
    organization_id: int,
    workflow_run_id: int,
    staff_user_id: int,
    ip_address: str | None,
) -> None:
    from api.services.privacy import access_log

    await access_log.record_access(
        session,
        organization_id=organization_id,
        user_id=staff_user_id,
        resource_type=access_log.RECORDING,
        resource_id=str(workflow_run_id),
        workflow_run_id=workflow_run_id,
        action="signed_url",
        actor_kind="staff",
        ip_address=ip_address,
    )
    await session.commit()


# --- the customer's side ------------------------------------------------------------


async def list_grants(session: AsyncSession, organization_id: int) -> list[dict]:
    rows = (
        await session.scalars(
            select(StaffContentGrantModel)
            .where(StaffContentGrantModel.organization_id == organization_id)
            .order_by(StaffContentGrantModel.granted_at.desc())
            .limit(100)
        )
    ).all()
    return [_view(r) for r in rows]


async def grant(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    workflow_run_id: int | None,
    days: int,
    reason: str | None,
) -> dict:
    if days < 1 or days > MAX_DAYS:
        raise GrantRefused(f"Choose between 1 and {MAX_DAYS} days.")
    if workflow_run_id is not None:
        # Only a call in this workspace (tenant isolation, api/AGENTS.md).
        owner = await run_organization(session, workflow_run_id)
        if owner != organization_id:
            raise LookupError("That call is not in this workspace.")
    now = datetime.now(UTC)
    row = StaffContentGrantModel(
        organization_id=organization_id,
        workflow_run_id=workflow_run_id,
        granted_by_user_id=user_id,
        reason=(reason or "").strip()[:300] or None,
        granted_at=now,
        expires_at=now + timedelta(days=days),
    )
    session.add(row)
    await session.flush()
    view = _view(row)
    await session.commit()
    return view


async def revoke(
    session: AsyncSession, *, organization_id: int, user_id: int, grant_id: int
) -> dict:
    row = await session.get(StaffContentGrantModel, grant_id)
    if row is None or row.organization_id != organization_id:
        raise LookupError("No such permission in this workspace.")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        row.revoked_by_user_id = user_id
        await session.flush()
    view = _view(row)
    await session.commit()
    return view


async def granted_runs(
    session: AsyncSession, pairs: list[tuple[int, int | None]]
) -> set[int]:
    """Of these (run id, workspace id) pairs, the runs a live grant covers.
    One query for a page of runs."""
    orgs = {org for _, org in pairs if org is not None}
    if not orgs:
        return set()
    now = datetime.now(UTC)
    g = StaffContentGrantModel
    rows = (
        await session.execute(
            select(g.organization_id, g.workflow_run_id).where(
                g.organization_id.in_(orgs),
                g.revoked_at.is_(None),
                g.expires_at > now,
            )
        )
    ).all()
    whole = {org for org, run in rows if run is None}
    single = {run for _, run in rows if run is not None}
    return {run for run, org in pairs if org in whole or run in single}
