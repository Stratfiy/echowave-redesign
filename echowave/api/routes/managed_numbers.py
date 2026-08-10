"""Buying, listing and releasing numbers Decibyl holds for a customer.

Thin handlers over :mod:`api.services.telephony.provisioning` and
:mod:`api.services.telephony.number_lifecycle`. Every route resolves the
organization from the authenticated user and passes it down; nothing here
trusts an id in a request body to imply ownership.

Release is the one operation that is not symmetrical with the others. It is
irreversible at the carrier, so the route requires a typed reason, records the
acting user, and refuses outright unless the service layer's grace period has
elapsed — the check lives there, not here, so it cannot be skipped by a second
caller.
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.kyc.plivo_compliance import PlivoComplianceError
from api.services.telephony import number_lifecycle, provisioning

router = APIRouter(prefix="/managed-numbers", tags=["managed-numbers"])


class NumberSearchRequest(BaseModel):
    telephony_configuration_id: int
    country_iso: str = "IN"
    number_type: str = "local"
    city: Optional[str] = None
    pattern: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=20)


class ProvisionRequest(BaseModel):
    telephony_configuration_id: int
    address: str
    label: Optional[str] = None
    inbound_workflow_id: Optional[int] = None
    country_code: str = "IN"


class ReleaseRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=3,
        description=(
            "Why this number is being released. Required: release is "
            "irreversible and the number may be printed on the customer's "
            "signage."
        ),
    )
    # Not exposed as a way to skip the grace period casually — the service
    # layer still demands an actor and a reason, and logs a forced release
    # distinctly.
    force: bool = False


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


async def _ensure_config_belongs_to_org(config_id: int, organization_id: int):
    configuration = await db_client.get_telephony_configuration_for_org(
        config_id, organization_id
    )
    if configuration is None:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")
    return configuration


@router.post("/search")
async def search_numbers(
    request: NumberSearchRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """Numbers the carrier has for sale right now.

    An empty list is a normal answer — India local inventory is thin and
    moves — not an error to retry.
    """
    organization_id = _organization_id(user)
    await _ensure_config_belongs_to_org(
        request.telephony_configuration_id, organization_id
    )

    try:
        results = await provisioning.search(
            organization_id=organization_id,
            telephony_configuration_id=request.telephony_configuration_id,
            country_iso=request.country_iso,
            number_type=request.number_type,
            city=request.city,
            pattern=request.pattern,
            limit=request.limit,
        )
    except provisioning.NotVerified as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, PlivoComplianceError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "numbers": [
            {
                "number": n.number,
                "country_iso": n.country_iso,
                "number_type": n.number_type,
                "city": n.city,
                "region": n.region,
                "monthly_rental": n.monthly_rental,
                "setup_price": n.setup_price,
            }
            for n in results
        ]
    }


@router.post("")
async def provision_number(
    request: ProvisionRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """Buy a number and start its monthly rental."""
    organization_id = _organization_id(user)
    await _ensure_config_belongs_to_org(
        request.telephony_configuration_id, organization_id
    )

    if request.inbound_workflow_id is not None:
        workflow = await db_client.get_workflow(
            request.inbound_workflow_id, organization_id=organization_id
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")

    try:
        result = await provisioning.provision(
            organization_id=organization_id,
            telephony_configuration_id=request.telephony_configuration_id,
            address=request.address,
            label=request.label,
            inbound_workflow_id=request.inbound_workflow_id,
            country_code=request.country_code,
        )
    except provisioning.NotVerified as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except provisioning.ProvisioningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlivoComplianceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "phone_number_id": result.phone_number_id,
        "address": result.address,
        "carrier_number_id": result.carrier_number_id,
        "recurring_charge_id": result.recurring_charge_id,
        "monthly_price_paise": result.monthly_price_paise,
    }


@router.post("/{phone_number_id}/release")
async def release_number(
    phone_number_id: int,
    request: ReleaseRequest,
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Give a number back to the carrier. Irreversible."""
    organization_id = _organization_id(user)

    number = await db_client.get_phone_number_for_org(phone_number_id, organization_id)
    if number is None:
        raise HTTPException(status_code=404, detail="Phone number not found")

    try:
        await number_lifecycle.release_number(
            phone_number_id=phone_number_id,
            actor_user_id=user.id,
            reason=request.reason,
            force=request.force,
        )
    except number_lifecycle.ReleaseNotPermitted as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"released": True, "phone_number_id": phone_number_id}
