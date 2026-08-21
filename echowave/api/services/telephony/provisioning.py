"""Buying a phone number for a customer, in an order that cannot leak money.

The order of operations here is the whole design, and it is not the obvious
one:

1. the compliance application must already be **accepted**
2. search
3. an autopay mandate must already be **authorised**
4. buy, with our application id set in the same request
5. link the compliance application to the number
6. only now write our database row and open the rental charge

Compliance first because a compliance application does not need a number, but a
number cannot be linked until one is accepted. Renting first and waiting would
mean paying carrier rent through a review that takes days and may end in a
rejection — a cost the customer has not agreed to and we could not bill for.

Autopay after search and before purchase, deliberately. Asking someone to
authorise a standing instruction before they have seen what is available is a
worse experience; handing them the number before they have authorised it is a
number we pay a carrier for every month with nothing standing behind the rent.

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
from api.services.telephony import registry as telephony_registry
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

    # Self-healing, for accounts approved before the verdict handler started
    # creating this. Idempotent and a no-op once one exists, so the cost is one
    # indexed read on the path where an approved account is about to search for
    # or buy a number — which is exactly the moment the configuration has to be
    # there. Without it those accounts sit on "No managed carrier account is
    # set up for this organisation yet" with no way forward that does not
    # involve a staff member.
    from api.services.kyc.service import ensure_managed_configuration

    await ensure_managed_configuration(organization_id)
    return record.carrier_reference


async def _assert_agreements(organization_id: int) -> None:
    """Refuse to sell a number to an account that has accepted nothing.

    Buying is the first moment the terms have teeth: it commits the customer to
    rent every month and commits us to a carrier contract in their name. A
    click-wrap is enforceable given notice, an affirmative act and a record —
    and the record is the part that matters in a dispute, which is why this is
    checked against stored acceptances rather than a checkbox in the request.

    Only the purchase path. Reading, signing in and everything an existing
    account already does are deliberately untouched: every account predating the
    agreements table has accepted nothing, and gating those would be an outage
    dressed as a compliance improvement.
    """
    from api.services.compliance import agreements

    async with db_client.async_session() as session:
        await agreements.require_accepted(session, organization_id=organization_id)


async def _assert_autopay(organization_id: int) -> int | None:
    """Refuse to hand over a number without a standing instruction behind it.

    Returns the mandate id to attach to the rental charge, or ``None`` when the
    requirement is switched off — the caller does not branch on which, so a
    deployment still waiting on the provider to activate Subscriptions runs the
    same code path and simply bills the prepaid balance.
    """
    from api.services.billing.mandates import assert_mandate_authorised

    async with db_client.async_session() as session:
        mandate = await assert_mandate_authorised(
            session, organization_id=organization_id
        )
        return mandate.id if mandate else None


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
    # Agreements before autopay, because nobody should authorise a standing
    # instruction under terms they have not been shown, and because accepting is
    # the cheaper of the two to fix.
    await _assert_agreements(organization_id)
    # Autopay is checked here and not in `search`, because the purchase flow is
    # documents -> approved -> search -> select -> mandate -> number. Showing a
    # customer the numbers on offer before asking them to authorise a standing
    # instruction is the right order; handing over the number before they have
    # is not. A number issued against an unauthorised mandate is a number we pay
    # a carrier for and cannot collect on.
    mandate_id = await _assert_autopay(organization_id)
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
    #
    # That was the intent all along and the call was missing: the only
    # protection here was `create_phone_number`'s unique constraint, which is
    # scoped to (organization_id, address_normalized) and therefore says
    # nothing about another org. Inbound dispatch keys on (provider,
    # account_id, address_normalized) — no organization — so two orgs sharing
    # the platform carrier account could both hold the same number, and
    # `find_inbound_route_by_account` would hand the call to whichever row the
    # database returned first.
    configuration = await db_client.get_telephony_configuration(
        telephony_configuration_id
    )
    spec = telephony_registry.get_optional(
        configuration.provider if configuration else ""
    )
    account_field = spec.account_id_credential_field if spec else ""
    account_id = (
        (
            (configuration.credentials or {}).get(account_field)
            if configuration
            else None
        )
        if account_field
        else None
    )
    if account_id:
        conflict = await db_client.find_inbound_routing_conflict(
            provider=configuration.provider,
            account_id_field=account_field,
            account_id=account_id,
            address=address,
            country_hint=country_code,
        )
        if conflict:
            existing_cfg, _existing_phone = conflict
            same_org = existing_cfg.organization_id == organization_id
            where = (
                f"telephony configuration '{existing_cfg.name}'"
                if same_org
                else "another organization on the same carrier account"
            )
            raise ProvisioningError(
                f"{address} is already routed for inbound calls by {where}. "
                "Buying it again would make inbound routing for this number "
                "ambiguous, so the purchase was not made."
            )

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

        # Priced through the plan when the account is on one, so a plan that
        # sells extra numbers at its own rate is a row rather than a branch.
        # Identical to NUMBER_RENTAL_PRICE_PAISE for an account with no plan,
        # and for a plan that sets no figure of its own.
        async with db_client.async_session() as pricing_session:
            price_paise = await rentals.next_number_price_paise(
                pricing_session, organization_id=organization_id
            )

        charge = await rentals.open_number_rental(
            organization_id=organization_id,
            phone_number_id=placeholder.id,
            cost_paise=NUMBER_RENTAL_COST_PAISE,
            price_paise=price_paise,
            mandate_id=mandate_id,
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
        if isinstance(exc, (ProvisioningError, NotVerified, PermissionError)):
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
        await db_client.delete_phone_number(phone_number_id, telephony_configuration_id)
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
