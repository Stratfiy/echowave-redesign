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

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.models import (
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    WorkflowModel,
)

#: The value recorded on the run, in the vocabulary ``billing/usage.py`` already
#: reads for the model components. ``managed`` means Decibyl fronted the
#: carrier and bills it on; ``byok`` means the customer's own carrier account
#: was behind the call and has already billed them directly.
MANAGED = "managed"
CUSTOMER_OWNED = "byok"


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
    candidates = [n.strip() for n in numbers if n and n.strip()]
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
                    TelephonyPhoneNumberModel.address.in_(candidates),
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
