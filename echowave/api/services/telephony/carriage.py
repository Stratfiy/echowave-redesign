"""Who paid the carrier for a call's minutes.

Every other cost on a call is ours by default: we buy the transcription, the
language model and the voice on our own keys, and a customer opting out of that
is the exception the pipeline records as ``byok``. Carriage is the other way
round. The default ``telephony_configurations.is_platform_managed`` is
``False`` — a configuration holding the *customer's* Twilio SID, Cloudonix
bearer token, Vobiz auth token or Asterisk box. Those minutes appear on the
customer's own carrier invoice, and a per-minute carriage line from us on top
of that is charging twice for one phone call.

So carriage needs the same question asked of it that key ownership asks of the
model services, and this module is where it is asked. The answer is recorded
onto the run at the moment the minutes are, because it is a fact about *that
call's* configuration — a customer who migrates from their own Twilio to a
Decibyl number should not have last month's calls re-read against this month's
setup.

**Unresolvable means unbilled, loudly.** A number we cannot trace to a
configuration is not evidence that we paid for it. Billing it anyway takes
money from a customer for something we cannot show we bought, which is worse
than the reverse: not billing carriage we did front is our own loss, it shows
up in the margin figures, and nobody is wrongly charged while we find it. The
fallback therefore logs, so a systematic resolution failure is visible as noise
rather than as a quietly shrinking telephony line.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import db_client
from api.db.models import (
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    WorkflowModel,
)
from api.utils.telephony_address import normalize_telephony_address

#: The value recorded on the run, in the vocabulary ``billing/usage.py`` already
#: reads for the model components. ``managed`` means Decibyl fronted the
#: carrier and bills it on; ``byok`` means the customer's own carrier account
#: was behind the call and has already billed them directly.
MANAGED = "managed"
CUSTOMER_OWNED = "byok"


@dataclass(frozen=True)
class CarriageBasis:
    """What carriage, if any, this account will be billed by us — for a quote.

    The same question as :func:`carriage_key_source`, asked *before* the call
    instead of after it, so an estimate can be right about the largest line it
    was silently getting wrong in both directions:

    * The create wizard passed no carrier at all, so it quoted a stack with no
      carriage — about 10% light for an account we do carry.
    * The Models screen passed the default outbound configuration's provider
      whatever it was, so an account on its **own** Twilio was quoted carriage
      we will never bill, on top of the invoice Twilio already sends them.

    Neither number was wrong by accident; both were confident. Hence
    ``explanation``: a price that excludes something has to say so, or the
    first invoice is where the customer finds out.
    """

    #: The carrier to price, or ``None`` when we will bill no carriage.
    provider: str | None
    #: ``managed`` | ``customer_carrier`` | ``no_carrier``.
    reason: str
    #: One sentence to put under the price.
    explanation: str


#: No number is connected yet, so there is nothing to say about a carrier.
NO_CARRIER = "no_carrier"
#: The account dials on its own carrier account. We bill no carriage at all.
CUSTOMER_CARRIER = "customer_carrier"


async def billable_carrier(
    session: AsyncSession, *, organization_id: int
) -> CarriageBasis:
    """Which carrier's minutes we would put on this account's invoice.

    Reads the default outbound configuration, falling back to the first — the
    same choice ``telephony/factory.py`` makes when it has nothing else to go
    on, so a quote is priced on the configuration a call would actually leave
    through.
    """
    rows = (
        (
            await session.execute(
                select(TelephonyConfigurationModel)
                .where(TelephonyConfigurationModel.organization_id == organization_id)
                .order_by(
                    TelephonyConfigurationModel.is_default_outbound.desc(),
                    TelephonyConfigurationModel.id,
                )
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        return CarriageBasis(
            provider=None,
            reason=NO_CARRIER,
            explanation=(
                "Excludes call charges — no phone number is connected yet. "
                "Connecting one adds the per-minute carrier cost."
            ),
        )

    chosen = rows[0]
    if not chosen.is_platform_managed:
        return CarriageBasis(
            provider=None,
            reason=CUSTOMER_CARRIER,
            explanation=(
                f"Excludes call charges — you dial on your own {chosen.provider} "
                "account, so those minutes are on their invoice, not ours."
            ),
        )

    return CarriageBasis(
        provider=chosen.provider,
        reason=MANAGED,
        explanation=f"Includes {chosen.provider} call charges.",
    )


async def carriage_key_source(*, workflow_id: int, numbers: list[str]) -> str:
    """``managed`` when Decibyl's carrier account served this call.

    ``numbers`` is every number the call touched — the caller ID on an outbound
    call, the dialled number on an inbound one. Only one of them is ours and
    which one depends on direction, so both are offered and whichever matches a
    configuration we own answers the question.

    Takes the workflow rather than the organization because ``workflow_runs``
    carries no ``organization_id`` of its own; the owning account is reached
    through ``workflows``, and doing that join here keeps the tenant check at
    the query level. A number string is not proof of ownership, and matching
    one across tenants would let another account's platform-managed
    configuration decide this account's bill.
    """
    #: Against ``address_normalized``, never ``address``. ``address`` is stored
    #: verbatim for display -- "+91 80 3530 2788", spaces and all -- while a
    #: carrier callback carries bare digits ("918035302788") or E.164
    #: ("+918035302788") depending on the field. Comparing those to the display
    #: form never matches, so every call fell through to the unresolved branch
    #: below and went unbilled. Every other lookup in this codebase already
    #: keys on the canonical form: shared_outbound, number_lifecycle, and the
    #: unique constraint itself, which is (organization_id, address_normalized).
    #:
    #: The failure was invisible while every configuration was BYOK, because
    #: unresolved and byok produce the same verdict -- do not bill. It becomes
    #: a silent revenue leak the moment a platform-managed configuration exists:
    #: Decibyl fronts the carrier, the match fails, and the carriage is written
    #: off with one WARNING per call and nothing in the ledger to find later.
    candidates: list[str] = []
    for raw in numbers:
        if not raw or not raw.strip():
            continue
        try:
            candidates.append(normalize_telephony_address(raw.strip()).canonical)
        except ValueError:
            # A number the normalizer refuses is a number we cannot own, so it
            # is dropped rather than raised: this runs on the billing path of a
            # call that has already happened.
            continue

    if not candidates:
        logger.warning(
            "Carriage key source unresolved for workflow {}: the callback "
            "carried no numbers. Not billing carriage for this call.",
            workflow_id,
        )
        return CUSTOMER_OWNED

    async with db_client.async_session() as session:
        managed = (
            await session.execute(
                select(TelephonyConfigurationModel.is_platform_managed)
                .join(
                    TelephonyPhoneNumberModel,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == TelephonyConfigurationModel.id,
                )
                .join(
                    WorkflowModel,
                    WorkflowModel.organization_id
                    == TelephonyPhoneNumberModel.organization_id,
                )
                .where(
                    WorkflowModel.id == workflow_id,
                    TelephonyPhoneNumberModel.address_normalized.in_(candidates),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

    if managed is None:
        logger.warning(
            "Carriage key source unresolved for workflow {}: none of {} maps to "
            "a telephony configuration this account owns. Not billing carriage "
            "for this call.",
            workflow_id,
            candidates,
        )
        return CUSTOMER_OWNED

    return MANAGED if managed else CUSTOMER_OWNED
