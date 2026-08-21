"""Carriage bills only for the one carrier we actually sell minutes on.

Two separate facts have to line up before a per-minute carriage charge is
honest, and the code used to check only one of them:

* **the configuration is ours** — ``is_platform_managed``, which defaults to
  False because the ordinary setup holds the customer's own carrier account;
* **we hold an account with that carrier** — ``RESOLD_CARRIERS``, which is one
  carrier today.

The flag alone is a claim somebody typed. A configuration marked managed on a
carrier we have no account with produces no invoice of ours, so passing a
charge through for it would invent a cost rather than recover one. That is the
failure this file pins, and it is the same shape as the double-charge the
carriage resolver was written for: money taken for something we cannot show we
bought.

The gate is deliberately silent-but-loud — the call still completes, the
carriage simply is not billed, and the mismatch is logged at error level,
because it means a configuration needs correcting or a carrier has genuinely
been taken on and belongs in the resold set with a rate beside it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.constants import RESOLD_CARRIERS
from api.db.models import (
    OrganizationModel,
    ProviderRateModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    WorkflowModel,
)
from api.services.billing.estimator import estimate_cost_per_minute
from api.services.telephony.carriage import (
    CUSTOMER_OWNED,
    MANAGED,
    carriage_key_source,
)

OUR_NUMBER = "+911140001234"


async def _account(session, slug: str, *, provider: str, managed: bool):
    """An organization with one number on one telephony configuration."""
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()

    configuration = TelephonyConfigurationModel(
        organization_id=org.id,
        name=slug,
        provider=provider,
        credentials={},
        is_platform_managed=managed,
    )
    session.add(configuration)
    await session.flush()

    session.add(
        TelephonyPhoneNumberModel(
            organization_id=org.id,
            telephony_configuration_id=configuration.id,
            address=OUR_NUMBER,
            address_normalized=OUR_NUMBER,
            address_type="phone",
        )
    )
    workflow = WorkflowModel(organization_id=org.id, name=f"wf-{slug}")
    session.add(workflow)
    await session.flush()
    return workflow


class TestOnlyTheCarrierWeSellBills:
    async def test_our_own_carrier_on_a_managed_configuration_bills(
        self, db_session, async_session
    ):
        """The case that must keep working, or we stop recovering a real cost."""
        carrier = sorted(RESOLD_CARRIERS)[0]
        workflow = await _account(
            async_session, "resold", provider=carrier, managed=True
        )

        source = await carriage_key_source(
            workflow_id=workflow.id, numbers=[OUR_NUMBER]
        )

        assert source == MANAGED

    @pytest.mark.parametrize("carrier", ["twilio", "cloudonix", "vobiz", "ari"])
    async def test_a_carrier_we_do_not_resell_bills_nothing_even_when_flagged(
        self, db_session, async_session, carrier
    ):
        """The flag is not the authority. We hold no account with these, so
        there is no invoice of ours to pass through and a charge would be
        invented rather than recovered."""
        assert carrier not in RESOLD_CARRIERS, (
            f"{carrier} is now resold, so this case is testing the wrong thing"
        )
        workflow = await _account(
            async_session, f"unsold-{carrier}", provider=carrier, managed=True
        )

        source = await carriage_key_source(
            workflow_id=workflow.id, numbers=[OUR_NUMBER]
        )

        assert source == CUSTOMER_OWNED

    async def test_the_customers_own_account_bills_nothing(
        self, db_session, async_session
    ):
        """The original double charge: their carrier already billed them."""
        carrier = sorted(RESOLD_CARRIERS)[0]
        workflow = await _account(async_session, "byo", provider=carrier, managed=False)

        source = await carriage_key_source(
            workflow_id=workflow.id, numbers=[OUR_NUMBER]
        )

        assert source == CUSTOMER_OWNED


class TestUnresolvableIsUnbilled:
    async def test_a_number_we_cannot_trace_bills_nothing(
        self, db_session, async_session
    ):
        carrier = sorted(RESOLD_CARRIERS)[0]
        workflow = await _account(
            async_session, "untraced", provider=carrier, managed=True
        )

        source = await carriage_key_source(
            workflow_id=workflow.id, numbers=["+919999999999"]
        )

        assert source == CUSTOMER_OWNED

    async def test_a_callback_with_no_numbers_bills_nothing(
        self, db_session, async_session
    ):
        carrier = sorted(RESOLD_CARRIERS)[0]
        workflow = await _account(
            async_session, "nonumbers", provider=carrier, managed=True
        )

        source = await carriage_key_source(workflow_id=workflow.id, numbers=["", "  "])

        assert source == CUSTOMER_OWNED

    async def test_another_accounts_managed_number_does_not_decide_this_bill(
        self, db_session, async_session
    ):
        """Tenant isolation, at the query level.

        A number string is not proof of ownership. Matching one across accounts
        would let a neighbour's platform-managed configuration put a carriage
        charge on this account's call.
        """
        carrier = sorted(RESOLD_CARRIERS)[0]
        await _account(async_session, "neighbour", provider=carrier, managed=True)
        theirs = await _account(async_session, "mine", provider=carrier, managed=False)

        source = await carriage_key_source(workflow_id=theirs.id, numbers=[OUR_NUMBER])

        assert source == CUSTOMER_OWNED


class TestTheQuoteAgreesWithTheBill:
    """The other half of the same rule, on the other document.

    Refusing to bill carriage is only right if we also stop quoting it. A
    customer shown a carriage line that never reaches their invoice has been
    quoted a different product — the same defect as the markup the estimator
    once omitted, pointing the other way, and just as invisible until somebody
    compares the two documents.
    """

    async def _priced(self, session, provider: str) -> None:
        session.add(
            ProviderRateModel(
                provider=provider,
                model="",
                component="telephony",
                unit="minute",
                rate_mpaise=55_000,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        await session.flush()

    async def test_a_carrier_we_sell_is_quoted(self, db_session, async_session):
        carrier = sorted(RESOLD_CARRIERS)[0]
        await self._priced(async_session, carrier)

        estimate = await estimate_cost_per_minute(
            async_session, organization_id=None, telephony_provider=carrier
        )

        assert estimate.telephony_paise_per_minute > 0

    @pytest.mark.parametrize("carrier", ["twilio", "cloudonix", "vobiz"])
    async def test_a_carrier_we_do_not_sell_is_not_quoted(
        self, db_session, async_session, carrier
    ):
        """Even with a rate on file. The rate is reference data; whether we
        sell the minutes is what decides if it applies."""
        await self._priced(async_session, carrier)

        estimate = await estimate_cost_per_minute(
            async_session, organization_id=None, telephony_provider=carrier
        )

        assert estimate.telephony_paise_per_minute == 0
        assert not [line for line in estimate.lines if line.component == "telephony"]

    async def test_carriage_we_do_not_sell_is_not_reported_as_unpriced(
        self, db_session, async_session
    ):
        """Absent for a reason, and the reason is not "we forgot the rate".

        ``unpriced`` means we buy something we cannot price, and the screens
        surface it as a gap to close. Carriage we do not sell is not a gap.
        """
        estimate = await estimate_cost_per_minute(
            async_session, organization_id=None, telephony_provider="twilio"
        )

        assert not [item for item in estimate.unpriced if item.startswith("telephony")]
