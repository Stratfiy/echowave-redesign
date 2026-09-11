"""The Sarvam voice service that speaks a clause at a time.

Pipecat does not take the aggregator as a constructor argument, so the swap is
made after construction. That is easy to get wrong silently -- a subclass that
forgets it is just the parent -- so the test constructs the real service and
looks at what it is holding.
"""

from __future__ import annotations

from pipecat.services.sarvam.tts import SarvamTTSService, SarvamTTSSettings
from pipecat.services.tts_service import TextAggregationMode
from pipecat.utils.text.base_text_aggregator import AggregationType

from api.services.billing.usage import provider_from_processor
from api.services.pipecat.clause_aggregator import ClauseTextAggregator
from api.services.pipecat.sarvam_tts import DecibylSarvamTTSService


def build(mode: TextAggregationMode = TextAggregationMode.SENTENCE):
    return DecibylSarvamTTSService(
        api_key="test-key",
        settings=SarvamTTSSettings(model="bulbul:v3", voice="anushka"),
        text_aggregation_mode=mode,
    )


class TestItFeedsClauses:
    def test_the_aggregator_is_the_clause_one(self):
        service = build()
        assert isinstance(service._text_aggregator, ClauseTextAggregator)

    def test_it_keeps_the_mode_it_was_given(self):
        """The factory says SENTENCE; the clause aggregator adds boundaries on
        top of that. Handed TOKEN it must pass tokens through, not clauses."""
        assert build()._text_aggregator._aggregation_type is AggregationType.SENTENCE
        token = build(TextAggregationMode.TOKEN)
        assert token._text_aggregator._aggregation_type is AggregationType.TOKEN

    def test_it_is_still_a_sarvam_service(self):
        """Nothing else changes: the websocket, the settings, the filters."""
        service = build()
        assert isinstance(service, SarvamTTSService)
        assert service._settings.model == "bulbul:v3"


class TestBillingStillSeesSarvam:
    def test_the_processor_name_prices_as_sarvam(self):
        """Usage keys carry the class name. A subclass that strips to
        "decibylsarvam" would price at nothing and report margin at 100%."""
        service = build()
        assert provider_from_processor(service.name) == "sarvam"
