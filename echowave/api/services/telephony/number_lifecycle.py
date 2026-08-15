"""The life of a managed number after it is bought: serving, release, audit.

Provisioning lives in :mod:`api.services.telephony.provisioning`; everything
that happens to a number afterwards lives here — whether it may carry a call
right now, giving it back to the carrier, and reconciling what we think we hold
against what the carrier bills us for.


Release is the only irreversible operation in the managed-number lifecycle.
A released number returns to the carrier's pool and can be reissued to someone
else within the day; it may also be the number on the customer's storefront,
their vehicles, and every invoice they have ever sent. So the release path is
built to be hard to trigger by accident and impossible to trigger silently:

* the grace period from :mod:`api.services.billing.dunning` must have elapsed,
  and is asserted here rather than assumed from the caller having checked
* an actor and a reason are required arguments, not optional ones
* the row is **soft-deleted**. It is the only record that we ever held the
  number, and deleting it is how an orphaned carrier rental becomes untraceable

The reconciliation job is the other half. Numbers can drift out of agreement
with the carrier in both directions, and the two directions fail differently:
a number Plivo bills us for that we have no record of is money leaving quietly,
and a number we route to that Plivo does not think we own is inbound calls
failing for a customer who is paying. Both are reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.models import (
    RecurringChargeModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
)
from api.enums import (
    PhoneNumberStatus,
    RecurringChargeStatus,
    RecurringChargeType,
)
from api.services.billing import dunning, rentals
from api.utils.telephony_address import normalize_telephony_address


class NumberSuspended(PermissionError):
    """A number cannot carry a call right now.

    Carries the status so a caller can distinguish "you have not paid" from
    "this number is gone", which need different sentences: one is fixed by a
    top-up and the other cannot be fixed at all.
    """

    def __init__(self, message: str, *, status: str) -> None:
        super().__init__(message)
        self.status = status


#: What to tell someone whose number will not take a call.
_UNAVAILABLE_MESSAGES = {
    PhoneNumberStatus.SUSPENDED.value: (
        "This number is paused because its monthly rental is unpaid. The "
        "number is still yours — top up your balance and calling resumes "
        "immediately."
    ),
    PhoneNumberStatus.RELEASED.value: (
        "This number has been released back to the carrier and can no longer "
        "take calls."
    ),
}


def assert_number_may_serve(number) -> None:
    """Refuse a call on a number that is suspended or released.

    Called on both legs. Suspension is the day-7 step of the dunning schedule,
    and it has to bite somewhere concrete or it is only a database column: this
    is that place. Deliberately separate from the KYC gate — an account can be
    fully verified and still not have paid for the number.

    A number with no status (rows predating the column) serves normally. The
    conservative reading would be to block, but blocking every number that
    existed before this feature would take a working platform down to fix a
    problem it did not have.
    """
    status = getattr(number, "status", None) or PhoneNumberStatus.ACTIVE.value
    if status == PhoneNumberStatus.ACTIVE.value:
        return
    raise NumberSuspended(
        _UNAVAILABLE_MESSAGES.get(status, "This number is not available for calls."),
        status=status,
    )


async def assert_configuration_may_serve(telephony_configuration_id: int) -> None:
    """Gate an outbound call on the caller ID it will go out as.

    Resolves the configuration's default caller ID and applies
    :func:`assert_number_may_serve` to it. A configuration with no default
    caller ID passes: the number will be chosen from the provider's configured
    list, which for a bring-your-own account is not ours to suspend.

    Lives here rather than in the route so the lookup is one call from the
    handler and the same gate is reachable from campaigns and any future
    caller.
    """
    if not telephony_configuration_id:
        return
    caller_id = await db_client.get_default_caller_id(telephony_configuration_id)
    if caller_id is None:
        return
    assert_number_may_serve(caller_id)


class ReleaseNotPermitted(PermissionError):
    """The grace period has not elapsed, or the number cannot be released.

    Raised rather than returning False so that a caller cannot proceed by
    ignoring a return value.
    """


@dataclass
class ReconciliationReport:
    """What the carrier holds versus what we think we hold."""

    configuration_id: int
    provider: str
    #: Rented at the carrier, absent from (or released in) our database. Every
    #: one of these is rent we are paying with nothing to bill it against.
    at_carrier_only: list[str] = field(default_factory=list)
    #: Active in our database, absent at the carrier. Inbound calls to these
    #: will fail, and a customer is paying for them.
    in_database_only: list[str] = field(default_factory=list)
    checked: int = 0
    error: str | None = None

    @property
    def clean(self) -> bool:
        return (
            not self.at_carrier_only
            and not self.in_database_only
            and self.error is None
        )


async def release_number(
    *,
    phone_number_id: int,
    actor_user_id: int | None,
    reason: str,
    force: bool = False,
    now: datetime | None = None,
) -> None:
    """Release a number at the carrier and record that we did.

    ``force`` skips the grace-period assertion and exists for the one
    legitimate case it does not cover: a customer asking to give a number up
    while fully paid. It does not skip the actor or the reason, and it is
    logged as a forced release so the distinction survives in the audit trail.
    """
    now = now or datetime.now(UTC)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError(
            "A release needs a reason. It is irreversible and someone will ask "
            "why it happened."
        )

    async with db_client.async_session() as session:
        number = await session.get(TelephonyPhoneNumberModel, phone_number_id)
        if number is None:
            raise ValueError(f"No phone number {phone_number_id}.")
        if number.status == PhoneNumberStatus.RELEASED.value:
            logger.info("Number {} is already released; nothing to do", number.address)
            return

        charge = await session.scalar(
            select(RecurringChargeModel).where(
                RecurringChargeModel.charge_type
                == RecurringChargeType.NUMBER_RENTAL.value,
                RecurringChargeModel.resource_id == phone_number_id,
                RecurringChargeModel.status.notin_(
                    [
                        RecurringChargeStatus.RELEASED.value,
                        RecurringChargeStatus.CANCELLED.value,
                    ]
                ),
            )
        )

        if not force:
            first_failed = charge.first_failed_at if charge else None
            if first_failed is not None and first_failed.tzinfo is None:
                first_failed = first_failed.replace(tzinfo=UTC)
            if not dunning.may_release(first_failed_at=first_failed, now=now):
                overdue = dunning.days_overdue(first_failed, now=now)
                raise ReleaseNotPermitted(
                    f"{number.address} has been unpaid for {overdue} day(s). "
                    f"Release is permitted after "
                    f"{dunning.RELEASE_ELIGIBLE_AFTER_DAYS} days. Suspend it "
                    "instead, or pass force for a customer-requested release."
                )

        configuration = await session.get(
            TelephonyConfigurationModel, number.telephony_configuration_id
        )
        organization_id = number.organization_id
        address = number.address

    from api.services.telephony.factory import get_telephony_provider_by_id

    provider = await get_telephony_provider_by_id(
        number.telephony_configuration_id, organization_id
    )
    if not provider.supports_number_management():
        raise ReleaseNotPermitted(
            f"{getattr(configuration, 'provider', 'This carrier')} does not "
            "support releasing numbers over the API. Release it in the "
            "carrier's console, then mark it released here."
        )

    result = await provider.release_number(address)
    if not result.ok:
        # Nothing is recorded as released. A row that says released while the
        # carrier still bills us is worse than a failed release, because it
        # stops anyone looking.
        raise RuntimeError(f"The carrier would not release {address}: {result.message}")

    async with db_client.async_session() as session:
        number = await session.get(TelephonyPhoneNumberModel, phone_number_id)
        number.status = PhoneNumberStatus.RELEASED.value
        number.is_active = False
        number.released_at = now
        number.released_by = actor_user_id
        number.release_reason = reason
        # Routing is cut here as well as by the status check, because inbound
        # dispatch resolves a workflow through this column and a released
        # number must not reach an agent.
        number.inbound_workflow_id = None
        await session.commit()

    await rentals.close_rental(
        phone_number_id=phone_number_id,
        status=RecurringChargeStatus.RELEASED,
        now=now,
    )

    logger.warning(
        "RELEASED number {} (org {}) by user {}{}: {}",
        address,
        organization_id,
        actor_user_id,
        " [forced]" if force else "",
        reason,
    )


async def reconcile_configuration(
    configuration: TelephonyConfigurationModel,
) -> ReconciliationReport:
    """Diff one carrier account's inventory against our own, both ways."""
    report = ReconciliationReport(
        configuration_id=configuration.id, provider=configuration.provider
    )

    from api.services.telephony.factory import get_telephony_provider_by_id

    try:
        provider = await get_telephony_provider_by_id(
            configuration.id, configuration.organization_id
        )
        if not provider.supports_number_management():
            return report
        carrier_numbers = await provider.list_owned_numbers()
    except Exception as exc:
        # A carrier we could not reach is not a carrier with no numbers.
        # Reporting an empty inventory here would mark every number we hold as
        # missing at the carrier, which is the shape of a false alarm that
        # trains people to ignore this job.
        report.error = f"Could not list numbers at the carrier: {exc}"
        logger.error("Reconciliation for config {} failed: {}", configuration.id, exc)
        return report

    async with db_client.async_session() as session:
        rows = list(
            (
                await session.scalars(
                    select(TelephonyPhoneNumberModel).where(
                        TelephonyPhoneNumberModel.telephony_configuration_id
                        == configuration.id,
                        TelephonyPhoneNumberModel.status
                        != PhoneNumberStatus.RELEASED.value,
                    )
                )
            ).all()
        )

    ours = {r.address_normalized: r for r in rows}
    theirs = {normalize_telephony_address(n).canonical: n for n in carrier_numbers if n}
    report.checked = len(theirs)

    for canonical, raw in theirs.items():
        if canonical not in ours:
            report.at_carrier_only.append(raw)

    for canonical, row in ours.items():
        # Numbers with no carrier id were added by hand against a customer's
        # own account and were never bought by us, so the carrier's inventory
        # says nothing about them.
        if row.carrier_number_id and canonical not in theirs:
            report.in_database_only.append(row.address)

    return report


async def reconcile_all() -> list[ReconciliationReport]:
    """Reconcile every platform-managed carrier account.

    Scoped to platform-managed configurations. A customer's own carrier account
    is theirs — we neither bought those numbers nor pay for them, and listing
    someone else's inventory to compare against ours would be both wrong and
    intrusive.
    """
    async with db_client.async_session() as session:
        configurations = list(
            (
                await session.scalars(
                    select(TelephonyConfigurationModel).where(
                        TelephonyConfigurationModel.is_platform_managed.is_(True)
                    )
                )
            ).all()
        )

    reports = []
    for configuration in configurations:
        try:
            reports.append(await reconcile_configuration(configuration))
        except Exception:
            logger.exception(
                "Reconciliation raised for configuration {}", configuration.id
            )
    return reports
