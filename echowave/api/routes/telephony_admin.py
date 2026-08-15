"""Staff administration of platform-managed telephony.

`is_platform_managed` is the flag that decides whether an organization's
numbers sit under Decibyl's carrier account or its own. Everything about
managed numbers hangs off it — the KYC gate, provisioning, rental billing —
and until this router existed the flag had exactly one writer
(`db_client.set_platform_managed`) and no caller, so the whole path was
reachable only by a hand-written `UPDATE`.

It is staff-only rather than customer-facing on purpose. A customer marking
their own configuration as platform-managed would be claiming our carrier
account and our compliance application, which is not theirs to claim. This is
a commercial decision someone at Decibyl makes.

Cross-account by definition, so the superuser gate is declared at router level
— a new endpoint added here is gated by default rather than by remembering.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.enums import PhoneNumberStatus
from api.services.auth.depends import get_superuser

router = APIRouter(
    prefix="/admin/telephony",
    tags=["admin-telephony"],
    dependencies=[Depends(get_superuser)],
)


class PlatformManagedRequest(BaseModel):
    managed: bool = True


def _configuration_summary(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "name": row.name,
        "provider": row.provider,
        "is_platform_managed": row.is_platform_managed,
        "is_default_outbound": row.is_default_outbound,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/configurations")
async def list_platform_managed_configurations(
    organization_id: int | None = None,
) -> dict[str, Any]:
    """Telephony configurations, optionally for one organization.

    Without an organization filter this returns only the platform-managed
    ones — the set staff actually administer. Listing every customer's own
    carrier configuration across the platform would be a lot of rows nobody
    asked for.
    """
    if organization_id is None:
        rows = await db_client.list_platform_managed_configurations()
    else:
        rows = await db_client.list_telephony_configurations(organization_id)

    return {"configurations": [_configuration_summary(r) for r in rows]}


@router.put("/configurations/{config_id}/platform-managed")
async def set_platform_managed(
    config_id: int,
    request: PlatformManagedRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Put an organization's telephony configuration on the managed path.

    Turning this **on** means: this organization's calls are gated on our KYC
    verification, and its numbers are ones we bought and pay rent for.

    Turning it **off** is refused while managed numbers are still attached.
    The flag is what marks those numbers as ours to bill and ours to release;
    clearing it would orphan them — we would keep paying the carrier with
    nothing left pointing at the rentals. Release the numbers first, then
    clear the flag.
    """
    row = await db_client.get_telephony_configuration(config_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")

    if not request.managed and row.is_platform_managed:
        numbers = await db_client.list_phone_numbers_for_config(config_id)
        held = [
            n
            for n in numbers
            if (n.status or PhoneNumberStatus.ACTIVE.value)
            != PhoneNumberStatus.RELEASED.value
            and n.carrier_number_id
        ]
        if held:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{len(held)} number(s) bought on our carrier account are "
                    "still attached to this configuration. Clearing the managed "
                    "flag would leave us paying rent on numbers nothing tracks. "
                    "Release them first: " + ", ".join(n.address for n in held[:5])
                ),
            )

    updated = await db_client.set_platform_managed(config_id, managed=request.managed)
    if updated is None:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")

    logger.warning(
        "Configuration {} (org {}) marked platform_managed={} by user {}",
        config_id,
        updated.organization_id,
        request.managed,
        user.id,
    )
    return _configuration_summary(updated)
