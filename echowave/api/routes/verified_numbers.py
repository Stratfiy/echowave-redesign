"""Numbers an organization has proved it can answer.

Scoped to the caller's own organization on every route. Verification is a claim
about a number *this account* controls; leaking it across accounts would let one
customer dial a number another customer verified, which is the harassment
vector the feature exists to close rather than a convenience to share.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.telephony import verified_numbers as service
from api.services.telephony.verification_sender import deliver_code

router = APIRouter(prefix="/verified-numbers", tags=["telephony"])


class StartRequest(BaseModel):
    phone_number: str = Field(..., max_length=32)
    label: str | None = Field(default=None, max_length=64)


class ConfirmRequest(BaseModel):
    phone_number: str = Field(..., max_length=32)
    code: str = Field(..., max_length=12)


class VerifiedNumber(BaseModel):
    phone_number: str
    label: str | None = None
    status: str
    verified_at: str | None = None


class StartResponse(BaseModel):
    phone_number: str
    #: Never the code. A verification whose code comes back in the response
    #: verifies nothing — the browser could simply read it and submit it.
    expires_in_seconds: int
    channel: str


@router.get("", response_model=list[VerifiedNumber])
async def list_numbers(user: UserModel = Depends(get_user)) -> list[VerifiedNumber]:
    records = await db_client.list_verified_numbers(user.selected_organization_id)
    return [
        VerifiedNumber(
            phone_number=record.phone_number,
            label=record.label,
            status=record.status,
            verified_at=record.verified_at.isoformat() if record.verified_at else None,
        )
        for record in records
    ]


@router.post("/start", response_model=StartResponse)
async def start(
    request: StartRequest, user: UserModel = Depends(get_user)
) -> StartResponse:
    """Issue a code and send it.

    The rate limits live in the service, not here, so the trigger API and any
    future admin path get them too rather than each route re-implementing them.
    """
    try:
        started = await service.start_verification(
            user.selected_organization_id,
            request.phone_number,
            user_id=user.id,
            label=request.label,
        )
    except service.NumberNotDialable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (service.TooManySends, service.ResendTooSoon) as exc:
        # 429 rather than 400: the request is well-formed and the answer is
        # "later", which is what a client needs to distinguish to back off.
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    delivery = await deliver_code(started.phone_number, started.code)
    if not delivery.ok:
        # The code is already stored. Reporting failure without saying "ask for
        # another" would leave the user waiting for a message that is not
        # coming, so the sender's message is the one shown.
        raise HTTPException(status_code=502, detail=delivery.error)

    remaining = (started.expires_at - datetime.now(UTC)).total_seconds()
    return StartResponse(
        phone_number=started.phone_number,
        expires_in_seconds=max(0, int(remaining)),
        channel=delivery.channel,
    )


@router.post("/confirm", response_model=VerifiedNumber)
async def confirm(
    request: ConfirmRequest, user: UserModel = Depends(get_user)
) -> VerifiedNumber:
    try:
        number = await service.confirm_verification(
            user.selected_organization_id, request.phone_number, request.code
        )
    except service.TooManyAttempts as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except service.VerificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = await db_client.get_verified_number(user.selected_organization_id, number)
    return VerifiedNumber(
        phone_number=record.phone_number,
        label=record.label,
        status=record.status,
        verified_at=record.verified_at.isoformat() if record.verified_at else None,
    )


@router.delete("/{phone_number}")
async def remove(phone_number: str, user: UserModel = Depends(get_user)) -> dict:
    from api.services.compliance.dnd import normalise_number

    normalised = normalise_number(phone_number)
    if normalised is None:
        raise HTTPException(status_code=400, detail="Not a phone number.")

    removed = await db_client.remove_verified_number(
        user.selected_organization_id, normalised
    )
    if not removed:
        raise HTTPException(status_code=404, detail="That number is not on the list.")
    return {"removed": normalised}
