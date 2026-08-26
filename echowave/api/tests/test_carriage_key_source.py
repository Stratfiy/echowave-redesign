"""The number on the callback and the number in the table are spelled differently.

``carriage_key_source`` decides whether Decibyl fronted the carrier for a call,
and it decides it by matching the numbers a carrier callback carried against
the numbers this account owns. Those two never agree on spelling.

``telephony_phone_numbers.address`` is stored **verbatim, for display** — the
row for the number these tests use reads ``+91 80 3530 2788``, spaces and all,
because that is what somebody typed. A Plivo hangup callback carries
``918035302788`` in ``From`` and ``+918035302788`` elsewhere in the same
payload. Matching a callback against the display column therefore never hits,
and the lookup fell through to "cannot resolve, do not bill" on every single
call.

**The bug was invisible for as long as every configuration was BYOK**, because
unresolved and byok reach the same verdict — no carriage line. It turns into a
silent revenue leak the moment one platform-managed configuration exists:
Decibyl pays the carrier for the minutes, the match misses, and the carriage is
written off with one WARNING per call and nothing in the ledger to find it by.

So the case worth pinning is not "a managed call bills carriage" in the
abstract. It is that a managed call bills carriage **when the stored address is
the display form and the callback sends bare digits** — which is every real
call, and was the one shape that failed.
"""

from __future__ import annotations

from api.db.models import (
    OrganizationModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    WorkflowModel,
)
from api.services.telephony import carriage

#: The three spellings one Plivo payload uses for one number, alongside the
#: display form the table holds. Every test here leans on the fact that these
#: are the same number and no string comparison of them says so.
DISPLAY = "+91 80 3530 2788"
CANONICAL = "+918035302788"
CALLBACK_BARE = "918035302788"
CALLBACK_E164 = "+918035302788"


async def _account(session, slug: str, *, managed: bool):
    """An organization owning one workflow, one configuration and one number."""
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()

    workflow = WorkflowModel(name=f"wf-{slug}", organization_id=org.id)
    session.add(workflow)
    await session.flush()

    config = TelephonyConfigurationModel(
        organization_id=org.id,
        name=f"cfg-{slug}",
        provider="plivo",
        credentials={"auth_id": "MA", "auth_token": "t"},
        is_platform_managed=managed,
    )
    session.add(config)
    await session.flush()

    number = TelephonyPhoneNumberModel(
        organization_id=org.id,
        telephony_configuration_id=config.id,
        # Verbatim, as the create path stores it, and deliberately not the
        # canonical form: a test that stored the canonical form here would pass
        # against the broken lookup too and prove nothing.
        address=DISPLAY,
        address_normalized=CANONICAL,
        address_type="pstn",
    )
    session.add(number)
    await session.flush()

    return org, workflow


class TestTheSpellingTheCallbackActuallySends:
    async def test_managed_call_bills_carriage_when_the_callback_sends_bare_digits(
        self, db_session, async_session
    ):
        """The regression. Bare digits are what ``From`` carries on a hangup."""
        _, workflow = await _account(async_session, "managed-bare", managed=True)

        source = await carriage.carriage_key_source(
            workflow_id=workflow.id,
            numbers=[CALLBACK_BARE, "917075701878"],
        )

        assert source == carriage.MANAGED

    async def test_managed_call_bills_carriage_when_the_callback_sends_e164(
        self, db_session, async_session
    ):
        """The same number, the other spelling, from the same payload."""
        _, workflow = await _account(async_session, "managed-e164", managed=True)

        source = await carriage.carriage_key_source(
            workflow_id=workflow.id,
            numbers=[CALLBACK_E164],
        )

        assert source == carriage.MANAGED

    async def test_customer_carrier_is_not_billed_carriage(
        self, db_session, async_session
    ):
        """Resolving is not the same as billing.

        A configuration holding the customer's own Plivo credentials resolves
        perfectly well and must still answer ``byok`` — those minutes are on
        their carrier invoice already, and a carriage line here charges twice
        for one phone call.
        """
        _, workflow = await _account(async_session, "byok", managed=False)

        source = await carriage.carriage_key_source(
            workflow_id=workflow.id,
            numbers=[CALLBACK_BARE],
        )

        assert source == carriage.CUSTOMER_OWNED

    async def test_a_number_this_account_does_not_own_is_never_billed(
        self, db_session, async_session
    ):
        """Tenancy, which the normalisation must not quietly widen.

        The lookup joins through ``workflows.organization_id``, so another
        account's platform-managed number must not decide this account's bill —
        even though the number now normalises to something matchable.
        """
        await _account(async_session, "theirs", managed=True)
        _, mine = await _account(async_session, "mine", managed=False)

        source = await carriage.carriage_key_source(
            workflow_id=mine.id,
            numbers=["918888888888"],
        )

        assert source == carriage.CUSTOMER_OWNED

    async def test_a_callback_carrying_no_numbers_is_not_billed(
        self, db_session, async_session
    ):
        """Unresolvable means unbilled, per the module docstring."""
        _, workflow = await _account(async_session, "empty", managed=True)

        source = await carriage.carriage_key_source(
            workflow_id=workflow.id, numbers=["", "   ", None]
        )

        assert source == carriage.CUSTOMER_OWNED
