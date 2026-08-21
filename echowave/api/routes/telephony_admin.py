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
from api.services.billing import carrier_rates

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
    verification, its numbers are ones we bought and pay rent for, and **we
    bill its minutes on**. That last one is why the carrier's rate has to be a
    real one: carriage is marked up into the sell price, so a stand-in rate
    either overcharges the customer or sells the minute at a loss, and the
    invoice reads the same either way. A carrier still on a seeded stand-in is
    refused here rather than discovered at month end —
    ``services/billing/carrier_rates.py``.

    Turning it **off** is refused while managed numbers are still attached.
    The flag is what marks those numbers as ours to bill and ours to release;
    clearing it would orphan them — we would keep paying the carrier with
    nothing left pointing at the rentals. Release the numbers first, then
    clear the flag.
    """
    row = await db_client.get_telephony_configuration(config_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")

    if request.managed:
        # Rate quality first: it is the more fundamental problem, and the one
        # that stays true even once a carrier is added to the allowlist below.
        async with db_client.async_session() as session:
            provisional = await carrier_rates.provisionally_priced(session)
        note = provisional.get((row.provider or "").strip().lower())
        if note is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{row.provider} has no verified India outbound rate — the "
                    f"one on file is a stand-in ({note}). Managed carriage is "
                    "marked up and billed on, so selling minutes at a figure "
                    "nobody has checked either overcharges this customer or "
                    "loses money on every call. Put the carrier's published "
                    "rate on /superadmin/billing/rate-card first, then mark "
                    "this configuration managed."
                ),
            )
        # Separately: even a carrier with a real, verified rate is not
        # automatically sellable — that is a rollout decision, not a pricing
        # one. See billing/carrier_rates.MANAGED_CARRIER_ALLOWLIST.
        if not carrier_rates.is_enabled_for_managed_billing(row.provider):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{row.provider} is not one of the carriers Decibyl currently "
                    "sells managed minutes on. This is a rollout decision, not a "
                    "pricing problem — see billing/carrier_rates.MANAGED_CARRIER_ALLOWLIST."
                ),
            )

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


class SharedOutboundRequest(BaseModel):
    shared: bool = True


@router.get("/shared-outbound")
async def list_shared_outbound() -> dict[str, Any]:
    """Decibyl's own caller IDs, lent to every account for outbound calls.

    This is the pool a trial account dials from when it has no carrier account
    of its own — the same job Twilio's +14157234000 does for caller-ID
    verification. Small by design and shared platform-wide, so the count here
    is the number of simultaneous trial calls the platform can place.
    """
    rows = await db_client.list_shared_outbound_numbers()
    return {
        "numbers": [
            {
                "id": row.id,
                "address": row.address,
                "telephony_configuration_id": row.telephony_configuration_id,
                "organization_id": row.organization_id,
                "is_active": row.is_active,
            }
            for row in rows
        ],
        # Said plainly because it is the operational limit that bites: a third
        # simultaneous trial call waits for one of these to free up.
        "concurrent_trial_calls": len(rows),
    }


@router.post("/phone-numbers/{phone_number_id}/shared-outbound")
async def set_shared_outbound(
    phone_number_id: int,
    request: SharedOutboundRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Lend one of our numbers to every account as an outbound caller ID.

    Staff-only, and a commercial decision rather than a customer one: the
    number stays ours, we keep paying its rent, and every account may dial out
    on it.

    Refuses a number that answers inbound calls. Inbound dispatch resolves the
    organization from the called number alone, so a number that is both shared
    and inbound-routable would answer one customer's caller inside whichever
    tenant the database returned first — the ambiguity the routing-conflict
    check exists to prevent.
    """
    row = await db_client.get_phone_number(phone_number_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Phone number not found")

    if request.shared and row.inbound_workflow_id is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{row.address} answers inbound calls, so it cannot also be a "
                "shared outbound caller ID — an inbound call to it could not "
                "say which account it belongs to. Detach the inbound agent "
                "first, or use a different number."
            ),
        )

    updated = await db_client.set_shared_outbound(
        phone_number_id, shared=request.shared
    )
    logger.warning(
        "Phone number {} ({}) marked shared_outbound={} by user {}",
        phone_number_id,
        updated.address,
        request.shared,
        user.id,
    )
    return {
        "id": updated.id,
        "address": updated.address,
        "is_shared_outbound": updated.is_shared_outbound,
    }
