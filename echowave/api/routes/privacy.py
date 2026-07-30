"""Data protection: retention, erasure, export and access history.

Every route is scoped to the caller's own organization. That is not a
convention here, it is the whole design: under DPDP our customer is the Data
Fiduciary for the people they call and we are the Processor, so we act on their
instruction about their own data and never on a stranger's request about an
account they have not proved they belong to. Answering "does this number appear
in that account" for an unauthenticated asker would itself be a disclosure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.constants import (
    GRIEVANCE_OFFICER_ADDRESS,
    GRIEVANCE_OFFICER_EMAIL,
    GRIEVANCE_OFFICER_NAME,
)
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.privacy import (
    access_log,
    erasure,
    export,
    metrics,
    retention,
    subprocessors,
)

router = APIRouter(prefix="/privacy", tags=["privacy"])


class RetentionRequest(BaseModel):
    recording_retention_days: int | None = Field(
        None, ge=1, description="Days to keep audio. Null uses the platform default."
    )
    transcript_retention_days: int | None = Field(
        None, ge=1, description="Days to keep transcripts and context."
    )


class ErasureRequest(BaseModel):
    phone_number: str = Field(
        ...,
        min_length=4,
        max_length=32,
        description="The number to erase from this account's calls.",
    )


def _organization_id(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user.selected_organization_id


@router.get("/retention")
async def get_retention(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """How long this account's call data is kept."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        policy = await retention.resolve_policy(
            session, organization_id=organization_id
        )
    return {
        "recording_retention_days": policy.recording_days,
        "transcript_retention_days": policy.transcript_days,
        "is_platform_default": policy.is_default,
        "minimum_days": retention.MINIMUM_RETENTION_DAYS,
    }


@router.put("/retention")
async def set_retention(
    request: RetentionRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """Set how long this account's call data is kept."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        try:
            policy = await retention.set_policy(
                session,
                organization_id=organization_id,
                recording_retention_days=request.recording_retention_days,
                transcript_retention_days=request.transcript_retention_days,
                updated_by=user.id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    return {
        "recording_retention_days": policy.recording_days,
        "transcript_retention_days": policy.transcript_days,
        "is_platform_default": policy.is_default,
    }


@router.post("/erasure")
async def request_erasure(
    request: ErasureRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """Erase one person's data from this account's calls.

    Irreversible, and deliberately so — a right to erasure satisfied by
    something recoverable is not satisfied.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        try:
            result = await erasure.erase_number(
                session,
                organization_id=organization_id,
                number=request.phone_number,
                requested_by=user.id,
            )
        except erasure.ErasureError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    return {
        "request_id": result.request_id,
        "status": result.status,
        "calls_erased": result.runs_affected,
        "files_deleted": result.objects_deleted,
    }


@router.get("/erasure")
async def list_erasure_requests(
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """This account's erasure history — the evidence obligations were met."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        requests = await erasure.list_requests(session, organization_id=organization_id)
    return {"requests": requests}


@router.get("/export")
async def export_data(
    phone_number: str | None = Query(
        None, description="Export one person's data. Omit for the whole account."
    ),
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Everything held, as JSON — GDPR Art 20 portability, DPDP s11 access."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        if phone_number:
            try:
                payload = await export.export_data_principal(
                    session, organization_id=organization_id, number=phone_number
                )
            except erasure.ErasureError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            payload = await export.export_organization(
                session, organization_id=organization_id
            )

        # An export is itself an access to personal data, so it is logged like
        # any other. A privacy tool exempt from the audit trail would be the
        # obvious way to read everything unobserved.
        await access_log.record_access(
            session,
            organization_id=organization_id,
            user_id=user.id,
            resource_type=access_log.EXPORT,
            resource_id=phone_number or "organization",
            action="export",
            actor_kind="session",
        )
        await session.commit()

    return payload


@router.get("/access-log")
async def get_access_log(
    workflow_run_id: int | None = Query(None),
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Who reached this account's recordings and transcripts."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        if workflow_run_id is not None:
            entries = await access_log.access_for_run(
                session,
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
            )
        else:
            entries = await access_log.recent_access(
                session, organization_id=organization_id
            )
    return {"entries": entries}


@router.get("/subprocessors")
async def list_subprocessors(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Who else has processed this account's data.

    Derived from the priced line items of this account's own calls, not from a
    platform-wide list: a customer who only ever used one model vendor should
    not be told their data went to five. GDPR Art 28(2), DPDP s11(1)(c).
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        listed = await subprocessors.for_organization(
            session, organization_id=organization_id
        )
    return {
        "subprocessors": [
            {
                "name": s.name,
                "purpose": s.purpose,
                "data": s.data,
                "basis": s.basis,
            }
            for s in listed
        ],
        "grievance_officer": {
            "name": GRIEVANCE_OFFICER_NAME or None,
            "email": GRIEVANCE_OFFICER_EMAIL or None,
            "address": GRIEVANCE_OFFICER_ADDRESS or None,
        },
    }


@router.get("/breach-report")
async def breach_report(
    since: str = Query(..., description="ISO timestamp — start of the window"),
    until: str | None = Query(None, description="ISO timestamp — defaults to now"),
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """What was reached between two times.

    GDPR Art 33 gives 72 hours from becoming aware of a breach to describe its
    scope. That question cannot be answered retrospectively — either the access
    log was being written or it was not — so this is the report that turns the
    log into an answer.
    """
    organization_id = _organization_id(user)
    try:
        start = datetime.fromisoformat(since)
        end = datetime.fromisoformat(until) if until else datetime.now(UTC)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Timestamps must be ISO 8601."
        ) from exc

    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    if start >= end:
        raise HTTPException(status_code=400, detail="'since' must precede 'until'.")

    async with db_client.async_session() as session:
        report = await access_log.window_report(
            session, organization_id=organization_id, start=start, end=end
        )
    return report


@router.get("/metrics")
async def privacy_metrics(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Whether the privacy controls are actually working.

    Every control on this router reports success by not raising, which is the
    wrong kind of quiet: a retention sweep that silently stopped running looks
    exactly like one with nothing to do. The headline here is designed to be
    zero — recordings past their window that still exist — and any other value
    is an incident rather than a statistic.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        return await metrics.snapshot(session, organization_id=organization_id)
