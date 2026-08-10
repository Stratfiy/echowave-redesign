"""Buying a phone number for a customer, in an order that cannot leak money.

The order of operations here is the whole design, and it is not the obvious
one:

1. the compliance application must already be **accepted**
2. search
3. buy, with our application id set in the same request
4. link the compliance application to the number
5. only now write our database row and open the rental charge

Compliance first because a compliance application does not need a number, but a
number cannot be linked until one is accepted. Renting first and waiting would
mean paying carrier rent through a review that takes days and may end in a
rejection — a cost the customer has not agreed to and we could not bill for.

Buy before link because Plivo will not link an application to a number the
account does not own. That ordering is forced, and it is what creates the only
genuinely dangerous window in this module: between step 3 and step 5 we are
paying for a number our database does not know about. Every failure after the
purchase therefore ends in :func:`_compensate` — release the number, log loudly,
and surface the original error. A number we cannot record is a number we must
not keep.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger

from api.constants import (
    NUMBER_RENTAL_COST_PAISE,
    NUMBER_RENTAL_PRICE_PAISE,
    PLATFORM_PLIVO_APPLICATION_ID,
)
from api.db import db_client
from api.enums import KycStatus, PhoneNumberStatus
from api.services.kyc.plivo_compliance import (
    PlivoComplianceClient,
    PlivoComplianceError,
)
from api.services.telephony.base import AvailableNumber


class ProvisioningError(RuntimeError):
    """Provisioning could not complete. Nothing is rented as a result.

    Every raise of this is preceded either by having bought nothing, or by a
    compensating release. If that invariant ever breaks, the symptom is a
    carrier invoice for a number nobody can find.
    """


class NotVerified(PermissionError):
    """The account has no accepted compliance application.

    Separate from ProvisioningError because it is the one failure that is the
    customer's to fix, and the route turns it into a 403 with the sentence
    telling them how.
    """


@dataclass(frozen=True)
class ProvisionedNumber:
    """What a successful purchase produced."""

    phone_number_id: int
    address: str
    carrier_number_id: str | None
    recurring_charge_id: int | None
    monthly_price_paise: int


async def assert_may_provision(organization_id: int) -> str:
    """Refuse to buy a number for an unverified account, and say why.

    Returns the compliance application id, which step 4 needs. Checked against
    our own record of the carrier's verdict rather than by asking Plivo: the
    callback has already told us, and an extra round trip against the tighter
    rate limit on every purchase buys nothing.
    """
    record = await db_client.get_kyc(organization_id)
    status = record.status if record else KycStatus.NOT_STARTED.value

    if status != KycStatus.CARRIER_APPROVED.value:
        raise NotVerified(
            "A phone number needs an approved telephony verification first. "
            f"This account is currently {status.replace('_', ' ')}."
        )
    if not record.carrier_reference:
        raise NotVerified(
            "This account is approved but carries no carrier application id, "
            "so a number cannot be linked to it. Re-run verification."
        )
    return record.carrier_reference


async def search(
    *,
    organization_id: int,
    telephony_configuration_id: int,
    country_iso: str = "IN",
    number_type: str = "local",
    city: str | None = None,
    pattern: str | None = None,
    limit: int = 20,
) -> list[AvailableNumber]:
    """What the carrier has for sale, for a customer who may buy one.

    Verification is checked before searching, not at purchase. Showing a
    customer a list they are not allowed to buy from is a worse experience than
    telling them what to do first.
    """
    await assert_may_provision(organization_id)
    provider = await _provider(telephony_configuration_id, organization_id)
    return await provider.search_available_numbers(
        country_iso=country_iso,
        number_type=number_type,
        city=city,
        pattern=pattern,
        limit=limit,
    )


async def provision(
    *,
    organization_id: int,
    telephony_configuration_id: int,
    address: str,
    label: str | None = None,
    inbound_workflow_id: int | None = None,
    country_code: str = "IN",
    compliance_client: PlivoComplianceClient | None = None,
) -> ProvisionedNumber:
    """Buy ``address`` for this organization and start charging rent for it.

    Idempotency: a placeholder row is written **before** the purchase, under
    the existing unique constraint on ``(organization_id, address_normalized)``
    and the global inbound-routing conflict check. A retry of the same request
    therefore collides on the database rather than at the carrier, and the
    second attempt never reaches ``buy_number``. This is what makes a timed-out
    purchase safe to retry: the failure mode we are protecting against is two
    numbers bought and one recorded.
    """
    compliance_id = await assert_may_provision(organization_id)
    provider = await _provider(telephony_configuration_id, organization_id)

    if not provider.supports_number_management():
        raise ProvisioningError(
            f"{getattr(provider, 'PROVIDER_NAME', 'This carrier')} cannot sell "
            "numbers through the API. Add the number by hand instead."
        )

    idempotency_key = uuid.uuid4().hex

    # -- 1. claim the number in our database, before spending anything ------
    #
    # Conflict handling deliberately reuses the same check the manual
    # add-a-number path uses, so a number already routed for anyone — this org
    # or another on the same carrier account — cannot be bought into an
    # ambiguous routing state.
    try:
        placeholder = await db_client.create_phone_number(
            organization_id=organization_id,
            telephony_configuration_id=telephony_configuration_id,
            address=address,
            country_code=country_code,
            label=label,
            inbound_workflow_id=inbound_workflow_id,
            is_active=False,
            is_default_caller_id=False,
            extra_metadata={"provisioning": "in_progress", "key": idempotency_key},
            status=PhoneNumberStatus.SUSPENDED.value,
        )
    except Exception as exc:
        raise ProvisioningError(
            f"Could not reserve {address} before purchase: {exc}"
        ) from exc

    bought = False
    try:
        # -- 2. buy, binding to our application in the same request --------
        purchase = await provider.buy_number(
            address,
            app_id=PLATFORM_PLIVO_APPLICATION_ID,
            idempotency_key=idempotency_key,
        )
        if not purchase.ok:
            raise ProvisioningError(
                f"The carrier would not sell {address}: {purchase.message}"
            )
        bought = True

        # -- 3. link the compliance application ----------------------------
        client = compliance_client or _compliance_client(provider)
        await client.link(
            number=address.lstrip("+"), compliance_application_id=compliance_id
        )

        # -- 4. record it as live and open the rental ----------------------
        await db_client.update_phone_number(
            phone_number_id=placeholder.id,
            telephony_configuration_id=telephony_configuration_id,
            is_active=True,
            status=PhoneNumberStatus.ACTIVE.value,
            carrier_number_id=purchase.carrier_number_id,
            provisioned_at=datetime.now(UTC),
            extra_metadata={"provisioned_by": "api", "key": idempotency_key},
        )

        from api.services.billing import rentals

        charge = await rentals.open_number_rental(
            organization_id=organization_id,
            phone_number_id=placeholder.id,
            cost_paise=NUMBER_RENTAL_COST_PAISE,
            price_paise=NUMBER_RENTAL_PRICE_PAISE,
        )
    except Exception as exc:
        await _compensate(
            provider=provider,
            phone_number_id=placeholder.id,
            telephony_configuration_id=telephony_configuration_id,
            address=address,
            bought=bought,
            cause=exc,
        )
        if isinstance(exc, (ProvisioningError, NotVerified)):
            raise
        raise ProvisioningError(f"Could not provision {address}: {exc}") from exc

    logger.info(
        "Provisioned {} for org {} (carrier id {}, charge {})",
        address,
        organization_id,
        purchase.carrier_number_id,
        charge.id if charge else None,
    )
    return ProvisionedNumber(
        phone_number_id=placeholder.id,
        address=address,
        carrier_number_id=purchase.carrier_number_id,
        recurring_charge_id=charge.id if charge else None,
        monthly_price_paise=NUMBER_RENTAL_PRICE_PAISE,
    )


async def _compensate(
    *,
    provider,
    phone_number_id: int,
    telephony_configuration_id: int,
    address: str,
    bought: bool,
    cause: Exception,
) -> None:
    """Undo a half-finished purchase.

    Two things can be true here, and both are handled: we may have bought the
    number, and we certainly have a placeholder row. The number is released
    first — that is the part that costs money every month — and the row is
    removed second.

    A failure *inside* the compensation is logged at error with the address
    spelled out, because that is the one case that produces exactly the
    orphaned rental this system exists to prevent, and the log line is the only
    thing standing between it and a carrier invoice nobody can reconcile.
    """
    if bought:
        try:
            result = await provider.release_number(address)
            if result.ok:
                logger.warning(
                    "Provisioning of {} failed after purchase ({}); released "
                    "the number back to the carrier",
                    address,
                    cause,
                )
            else:
                logger.error(
                    "ORPHANED CARRIER NUMBER {}: provisioning failed ({}) and "
                    "the compensating release also failed ({}). We are paying "
                    "rent on this number with no active record of it. Release "
                    "it by hand.",
                    address,
                    cause,
                    result.message,
                )
        except Exception as release_error:
            logger.error(
                "ORPHANED CARRIER NUMBER {}: provisioning failed ({}) and the "
                "compensating release raised ({}). We are paying rent on this "
                "number with no active record of it. Release it by hand.",
                address,
                cause,
                release_error,
            )

    try:
        await db_client.delete_phone_number(
            phone_number_id, telephony_configuration_id
        )
    except Exception as cleanup_error:
        logger.error(
            "Could not remove the placeholder row for {} after a failed "
            "provisioning: {}",
            address,
            cleanup_error,
        )


async def _provider(telephony_configuration_id: int, organization_id: int):
    from api.services.telephony.factory import get_telephony_provider_by_id

    return await get_telephony_provider_by_id(
        telephony_configuration_id, organization_id
    )


def _compliance_client(provider) -> PlivoComplianceClient:
    """A compliance client on the same account the number was bought from.

    Deliberately built from the provider's own credentials rather than from the
    platform constants: linking has to happen on the account that owns the
    number, and a mismatch between the two produces a confusing "number not
    found" from Plivo rather than an auth error.
    """
    auth_id = getattr(provider, "auth_id", None)
    auth_token = getattr(provider, "auth_token", None)
    if not auth_id or not auth_token:
        raise PlivoComplianceError(
            "The telephony configuration has no Plivo credentials, so the "
            "compliance application cannot be linked to the number."
        )
    return PlivoComplianceClient(auth_id, auth_token)
