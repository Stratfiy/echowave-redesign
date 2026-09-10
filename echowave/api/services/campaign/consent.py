"""The calling-consent attestation a campaign carries before it dials.

An outbound campaign is the platform calling people who did not call us.
Whether they agreed to that is a fact only the customer holds — it lives in
their enquiry forms, their CRM, their DLT registration — and the platform's
part is to ask, once per campaign, and keep the answer. The do-not-call
list is checked by us regardless; this is about the numbers that pass it.

Per campaign rather than per account: a complaint arrives about a call,
the call belongs to a campaign, and the question is who confirmed that
list. An account-level tick answers a different question.
"""

from __future__ import annotations

from datetime import UTC, datetime

from api.db import db_client

#: Bump when the wording changes what is being attested. Recorded nowhere
#: yet because there is only one; the day there are two, store it.
STATEMENT_VERSION = "2026-09"

STATEMENT = (
    "I confirm that everyone on this list has agreed to be called by us for "
    "this purpose, that the list excludes anyone who asked not to be called, "
    "and that any registration the telecom rules require for this calling "
    "(such as DLT in India) is in place."
)


class ConsentNotAttested(PermissionError):
    """The campaign has no attestation and this start did not carry one."""

    def __init__(self) -> None:
        super().__init__(
            "Confirm that the people on this list agreed to be called before "
            "starting the campaign."
        )


async def attest(*, campaign_id: int, user_id: int, now: datetime | None = None):
    """Record who confirmed the list. Idempotent: a second start keeps the first."""
    campaign = await db_client.get_campaign_by_id(campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")
    if campaign.consent_attested_at is not None:
        return campaign
    return await db_client.update_campaign(
        campaign_id,
        consent_attested_at=now or datetime.now(UTC),
        consent_attested_by=user_id,
    )


async def require_attested(*, campaign, user_id: int, attested_now: bool) -> None:
    """Either the campaign already carries an attestation or this start does."""
    if campaign.consent_attested_at is not None:
        return
    if not attested_now:
        raise ConsentNotAttested()
    await attest(campaign_id=campaign.id, user_id=user_id)
