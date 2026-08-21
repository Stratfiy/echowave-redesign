"""Every carrier we sell minutes on is priced, and no other carrier can bill.

The failure this prevents is quiet. A telephony provider with no rate row does
not raise, does not fail a call and does not appear on any screen — it lands in
``CallCost.uncosted``, contributes zero provider cost, and every margin figure
downstream reads better than it is.

The rule used to be "every carrier in the enum is priced or excused", which
sounds safer than it was: cloudonix and vobiz were satisfied with **invented**
numbers, placed at Twilio's India rate because nobody had looked up the real
ones. A made-up price in the rate card is one configuration change away from
billing a customer a figure we chose from nothing.

The rule now follows what we actually sell. ``RESOLD_CARRIERS`` names the
carriers we hold an account with — one, today — and only those need a price,
because only those can produce a carriage line at all. Minutes on a customer's
own carrier are on their invoice already and are never measured, so a carrier
outside that set having no rate is the accurate statement rather than a gap: we
buy nothing, so there is nothing to pass on.

That makes the guard two-sided. A carrier we resell without a rate fails here.
So does a carrier priced but never sold, once someone notices — because the
day it is added to ``RESOLD_CARRIERS`` its price must already be real.
"""

from __future__ import annotations

import pytest

from api.constants import RESOLD_CARRIERS
from api.enums import CostComponent, WorkflowRunMode
from api.services.billing.default_rates import TELEPHONY_RATES
from api.services.billing.usage import usage_items_from_usage_info

#: Carriers that will never carry a vendor rate, and the reason. ARI is a
#: self-hosted Asterisk: the SIP trunk behind it belongs to whoever runs the
#: box, so there is no price of ours to pass through.
_UNPRICED_BY_DESIGN = {
    "ari": "self-hosted Asterisk; the trunk and its bill belong to the customer",
}

#: Run modes that are not phone calls at all, so no carrier bills for them.
#: WebRTC and text chat carry no PSTN leg; the last three are historical values
#: the enum keeps for old rows and nothing dials on them.
_NON_CARRIER_MODES = {
    "webrtc",
    "smallwebrtc",
    "textchat",
    "stasis",
    "VOICE",
    "CHAT",
}


def _priced() -> set[str]:
    return {
        rate.provider
        for rate in TELEPHONY_RATES
        if rate.component is CostComponent.TELEPHONY
    }


class TestEveryCarrierWeSellIsPriced:
    def test_every_resold_carrier_has_a_rate(self):
        """The one-directional gap that costs money.

        Selling minutes on a carrier with no rate on file records the usage and
        prices it at nothing, so the margin on every such call reads as pure
        profit.
        """
        missing = RESOLD_CARRIERS - _priced()

        assert not missing, (
            f"we resell these carriers with no rate on file: {sorted(missing)}. "
            "Carriage we sell but cannot price is recorded as uncosted, and "
            "every margin figure that includes it reads better than it is."
        )

    def test_the_resold_set_is_not_empty(self):
        """Guards the test above from passing by having nothing to check."""
        assert RESOLD_CARRIERS, (
            "no carrier is marked as resold, so no carriage can be billed at "
            "all and the coverage check above is vacuous."
        )

    def test_a_carrier_we_do_not_resell_bills_nothing(self):
        """Stated as a property rather than assumed from the rate card.

        A rate on a carrier we do not sell is inert — ``carriage.py`` refuses
        to mark such a call managed — so rows for twilio, telnyx and vonage are
        reference figures, not live prices. This pins that the two lists are
        allowed to differ in that direction, and only that direction.
        """
        priced_but_not_sold = _priced() - RESOLD_CARRIERS
        for provider in priced_but_not_sold:
            assert provider not in RESOLD_CARRIERS

    @pytest.mark.parametrize("provider", sorted(_UNPRICED_BY_DESIGN))
    def test_a_deliberate_exclusion_carries_its_reason(self, provider):
        assert _UNPRICED_BY_DESIGN[provider].strip()

    @pytest.mark.parametrize("provider", sorted(_UNPRICED_BY_DESIGN))
    def test_a_deliberate_exclusion_is_not_also_priced(self, provider):
        assert provider not in _priced(), (
            f"{provider} is listed as unpriced by design but has a rate row. "
            "One of the two is wrong."
        )

    def test_a_carrier_that_is_neither_sold_nor_explained_is_still_visible(self):
        """Not a failure — a record of what a carrier's silence means.

        A run mode with no rate and no entry in ``_UNPRICED_BY_DESIGN`` used to
        fail here. It no longer does, because not pricing a carrier we never
        sell is now correct. What must stay true is that such a carrier cannot
        bill, which the carriage gate enforces and which this names so the
        change in rule is not mistaken for a check that was dropped.
        """
        supported = {mode.value for mode in WorkflowRunMode}
        unsold = supported - RESOLD_CARRIERS - _NON_CARRIER_MODES
        assert unsold.isdisjoint(RESOLD_CARRIERS)


class TestNoInventedPrices:
    def test_no_rate_is_a_placeholder(self):
        """A guessed price must not exist, rather than merely read as guessed.

        This test used to accept a placeholder as long as it was labelled and
        non-zero. That was the wrong bar: a labelled invention still bills real
        money the moment its carrier becomes managed, and the label is only
        seen by somebody already auditing the rate card.
        """
        invented = [r.provider for r in TELEPHONY_RATES if "PLACEHOLDER" in r.basis]

        assert not invented, (
            f"these carriage rates are invented figures: {sorted(invented)}. "
            "Price a carrier from its published rate or do not price it — a "
            "carrier we do not resell needs no row, and one we do resell needs "
            "a real one."
        )

    def test_no_carrier_is_priced_at_zero(self):
        for rate in TELEPHONY_RATES:
            assert rate.usd_per_unit > 0, (
                f"{rate.provider} carriage is priced at zero, which reports as "
                "costed while contributing nothing"
            )


class TestCarriageIsOnlyBilledWhenWeBoughtIt:
    """The double charge, and the shape of its fix.

    ``is_platform_managed`` defaults to False, so the ordinary telephony
    configuration is the customer's own carrier account. Those minutes are
    already on their Twilio or Vobiz invoice. Billing a carriage line here as
    well charged twice for one phone call — at Rs1.20 a minute on Twilio, which
    on a 10,000-minute month is Rs12,000 of somebody else's electricity.
    """

    def test_a_customer_owned_carrier_produces_no_carriage_line(self):
        items = usage_items_from_usage_info(
            {
                "telephony": {"twilio": 120},
                "key_sources": {"telephony": "byok"},
            }
        )
        assert not [i for i in items if i.component is CostComponent.TELEPHONY]

    def test_our_own_carrier_still_bills(self):
        items = usage_items_from_usage_info(
            {
                "telephony": {"twilio": 120},
                "key_sources": {"telephony": "managed"},
            }
        )
        telephony = [i for i in items if i.component is CostComponent.TELEPHONY]
        assert len(telephony) == 1
        assert telephony[0].quantity == 120
        assert telephony[0].provider == "twilio"

    def test_an_unrecorded_carrier_is_not_billed(self):
        """The safe direction, and the opposite of the model components'.

        An unrecorded model key source means we most likely bought the
        inference. An unrecorded carrier means we cannot show we bought the
        minutes, and charging for those takes the customer's money for
        something we cannot evidence.
        """
        items = usage_items_from_usage_info({"telephony": {"twilio": 120}})
        assert not [i for i in items if i.component is CostComponent.TELEPHONY]

    def test_carriage_ownership_does_not_disturb_the_model_components(self):
        """A BYO carrier is not a BYO language model."""
        items = usage_items_from_usage_info(
            {
                "telephony": {"twilio": 120},
                "tts": {"sarvam|||bulbul:v2": 2300},
                "key_sources": {"telephony": "byok"},
            }
        )
        assert [i.component for i in items] == [CostComponent.TTS]

    def test_the_model_components_still_default_to_managed(self):
        """Unchanged, and the asymmetry is the point of the two defaults."""
        items = usage_items_from_usage_info({"tts": {"sarvam|||bulbul:v2": 2300}})
        assert [i.component for i in items] == [CostComponent.TTS]


class TestOnlyOurOwnCarriageIsMeasured:
    """Not measured, not merely unbilled.

    Recording seconds on a customer's own carrier and then declining to charge
    for them would leave a telephony figure on every internal report that
    nobody could act on — and one that reads, on a margin screen, exactly like
    carriage we paid for and forgot to bill.

    The platform fee is unaffected either way: billable time comes from
    ``call_duration_seconds``, written by the pipeline, so a call on the
    customer's own number still bills for the minutes it ran.
    """

    def test_a_run_with_no_telephony_block_still_has_billable_time(self):
        from api.services.billing.usage import billable_seconds_from_usage_info

        # What a completed call on a customer's own carrier now looks like.
        usage_info = {
            "call_duration_seconds": 143,
            "tts": {"sarvam|||bulbul:v2": 2300},
        }

        assert billable_seconds_from_usage_info(usage_info) == 143
        assert not [
            i
            for i in usage_items_from_usage_info(usage_info)
            if i.component is CostComponent.TELEPHONY
        ]

    def test_our_own_number_still_records_and_bills(self):
        usage_info = {
            "call_duration_seconds": 143,
            "telephony": {"plivo": 143},
            "key_sources": {"telephony": "managed"},
        }
        items = usage_items_from_usage_info(usage_info)
        telephony = [i for i in items if i.component is CostComponent.TELEPHONY]

        assert len(telephony) == 1
        assert telephony[0].provider == "plivo"
        assert telephony[0].quantity == 143
