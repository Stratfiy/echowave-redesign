"""Data protection: retention, erasure, export and access history.

Every route is scoped to the caller's own organization. That is not a
convention here, it is the whole design: under DPDP our customer is the Data
Fiduciary for the people they call and we are the Processor, so we act on their
instruction about their own data and never on a stranger's request about an
account they have not proved they belong to. Answering "does this number appear
in that account" for an unauthenticated asker would itself be a disclosure.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.privacy import access_log, erasure, export, retention

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
