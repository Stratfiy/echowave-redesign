"""Every carrier we can dial on is either priced or deliberately not.

The failure this prevents is quiet. A telephony provider with no rate row does
not raise, does not fail a call and does not appear on any screen — it lands in
``CallCost.uncosted``, contributes zero provider cost, and every margin figure
downstream reads better than it is. Three carriers sat like that: cloudonix,
vobiz and ari, all reachable from the provider enum, none of them in the rate
card.

So the rule is coverage with one documented exception, rather than a list
somebody remembers to update. Adding a provider to the enum without a rate row
fails here, and the only way past is to price it or to say in
``_UNPRICED_BY_DESIGN`` why it will never have a vendor price.
"""

from __future__ import annotations

import pytest

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


class TestEveryCarrierIsAccountedFor:
    def test_no_carrier_is_silently_unpriced(self):
        supported = {mode.value for mode in WorkflowRunMode}
        # Only the modes that are actually carriers; the enum also carries
        # non-telephony run modes such as web calls.
        carriers = supported & (_priced() | set(_UNPRICED_BY_DESIGN))
        missing = supported - carriers - _NON_CARRIER_MODES

        assert not missing, (
            "these run modes are neither priced nor listed as unpriced by "
            f"design: {sorted(missing)}. An unpriced carrier bills the customer "
            "nothing for carriage and overstates margin on every call."
        )

    @pytest.mark.parametrize("provider", sorted(_UNPRICED_BY_DESIGN))
    def test_a_deliberate_exclusion_carries_its_reason(self, provider):
        assert _UNPRICED_BY_DESIGN[provider].strip()

    @pytest.mark.parametrize("provider", sorted(_UNPRICED_BY_DESIGN))
    def test_a_deliberate_exclusion_is_not_also_priced(self, provider):
        assert provider not in _priced(), (
            f"{provider} is listed as unpriced by design but has a rate row. "
            "One of the two is wrong."
        )


class TestThePlaceholdersSaySo:
    def test_a_guessed_rate_is_labelled_in_its_basis(self):
        """A guessed price must read as one to whoever audits the rate card.

        The marker is also what the managed path refuses on — see
        ``test_carrier_rates_are_real.py``, which owns that rule.
        """
        from api.services.billing.default_rates import PROVISIONAL_MARKER

        for rate in TELEPHONY_RATES:
            if rate.provisional:
                assert PROVISIONAL_MARKER in rate.basis
                assert rate.usd_per_unit > 0, (
                    f"{rate.provider} is a stand-in priced at zero, which is "
                    "the under-reporting this file exists to prevent"
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
