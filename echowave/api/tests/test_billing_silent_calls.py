"""A call that delivered nothing is not charged our fee.

A provider that fails to connect leaves the agent mute. The caller hears
silence, gives up, and the run reaches costing looking like any other completed
call — so it is billed one pulse of platform fee for a conversation that never
happened. Small money, expensive to explain.

The rule is a conjunction, and these tests exist mainly to hold it there. A
false waiver is a silent revenue leak, which is worse than the defect it would
be fixing, so a fee is given up only when something demonstrably broke *and*
nothing was said. Everything else pays.
"""

from __future__ import annotations

from api.enums import CostComponent, RateUnit
from api.services.billing.cost_engine import RateSpec, UsageItem, compute_call_cost
from api.services.billing.delivery import (
    agent_spoke,
    platform_fee_is_waived,
    recorded_failure,
)
from api.services.billing.money import DEFAULT_PLATFORM_RATE_MPAISE

ERROR = {
    "pipeline_error": {"detail": "Error connecting: no close frame received or sent"}
}
SPOKE_TTS = {"tts": {"sarvam|||bulbul:v2": 412}}
SPOKE_TEXT = {
    "realtime_feedback_events": [
        {"type": "rtf-bot-text", "payload": {"text": "Hello, how can I help?"}}
    ]
}


class TestAgentSpoke:
    def test_tts_characters_are_evidence(self):
        assert agent_spoke(usage_info=SPOKE_TTS, logs=None) is True

    def test_a_bot_text_event_is_evidence(self):
        assert agent_spoke(usage_info=None, logs=SPOKE_TEXT) is True

    def test_zero_tts_characters_are_not_evidence(self):
        assert (
            agent_spoke(usage_info={"tts": {"sarvam|||bulbul:v2": 0}}, logs=None)
            is False
        )

    def test_an_empty_bot_line_is_not_evidence(self):
        logs = {
            "realtime_feedback_events": [
                {"type": "rtf-bot-text", "payload": {"text": ""}}
            ]
        }
        assert agent_spoke(usage_info=None, logs=logs) is False

    def test_the_caller_speaking_is_not_the_agent_speaking(self):
        """The exact shape of the failure: the human talks, we never answer."""
        logs = {
            "realtime_feedback_events": [
                {
                    "type": "rtf-user-transcription",
                    "payload": {"final": True, "text": "Hello?"},
                }
            ]
        }
        assert agent_spoke(usage_info=None, logs=logs) is False

    def test_nothing_at_all_is_not_evidence(self):
        assert agent_spoke(usage_info=None, logs=None) is False
        assert agent_spoke(usage_info={}, logs={}) is False

    def test_malformed_shapes_do_not_raise(self):
        assert (
            agent_spoke(
                usage_info={"tts": "nonsense"}, logs={"realtime_feedback_events": 7}
            )
            is False
        )
        assert agent_spoke(usage_info=[], logs=[1, 2, 3]) is False


class TestRecordedFailure:
    def test_a_recorded_detail_counts(self):
        assert recorded_failure(extra=ERROR) is True

    def test_fatality_is_not_asked(self):
        """A non-fatal error on a silent call did not survive anything."""
        assert (
            recorded_failure(
                extra={"pipeline_error": {"detail": "boom", "fatal": False}}
            )
            is True
        )

    def test_no_error_is_no_failure(self):
        assert recorded_failure(extra=None) is False
        assert recorded_failure(extra={}) is False
        assert recorded_failure(extra={"pipeline_error": {}}) is False
        assert recorded_failure(extra={"pipeline_error": {"detail": "   "}}) is False


class TestTheConjunction:
    def test_error_and_silence_waives(self):
        assert platform_fee_is_waived(usage_info={}, logs={}, extra=ERROR) is True

    def test_an_error_on_a_call_that_talked_still_pays(self):
        """A provider grumbled and the agent kept talking. That is a delivered call."""
        assert (
            platform_fee_is_waived(usage_info=SPOKE_TTS, logs={}, extra=ERROR) is False
        )

    def test_silence_with_no_error_still_pays(self):
        """The guard against waiving on a shape we simply cannot read.

        A speech-to-speech realtime model may bill audio tokens rather than TTS
        characters. Requiring a recorded error keeps that class of working call
        out of this path entirely, even if neither speech signal fires.
        """
        assert platform_fee_is_waived(usage_info={}, logs={}, extra={}) is False

    def test_an_ordinary_healthy_call_pays(self):
        assert (
            platform_fee_is_waived(usage_info=SPOKE_TTS, logs=SPOKE_TEXT, extra={})
            is False
        )


class TestWhatTheReceiptShows:
    def _usage(self):
        return [
            UsageItem(
                component=CostComponent.TELEPHONY,
                provider="plivo",
                model="",
                quantity=15,
            )
        ]

    def test_a_waived_call_has_no_platform_line(self):
        cost = compute_call_cost(
            billable_seconds=3,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            pulse_seconds=15,
            usage=self._usage(),
            provider_rates={
                ("telephony", "plivo", ""): RateSpec(
                    rate_mpaise=38_000, unit=RateUnit.MINUTE
                )
            },
            platform_fee_waived=True,
        )
        assert cost.platform_fee_paise == 0
        assert cost.platform_fee_waived is True
        assert all(line.component != "platform" for line in cost.line_items)

    def test_the_provider_is_still_paid_for(self):
        """We paid the carrier for those seconds whether or not the call worked."""
        cost = compute_call_cost(
            billable_seconds=3,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            pulse_seconds=15,
            usage=self._usage(),
            provider_rates={
                ("telephony", "plivo", ""): RateSpec(
                    rate_mpaise=38_000, unit=RateUnit.MINUTE
                )
            },
            platform_fee_waived=True,
        )
        assert cost.total_provider_cost_paise > 0
        assert cost.total_charged_paise == sum(
            line.cost_paise for line in cost.line_items
        )

    def test_the_same_call_unwaived_charges_one_pulse(self):
        """The regression this fixes: 3 seconds billed as 15 at ₹3.00/min."""
        cost = compute_call_cost(
            billable_seconds=3,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            pulse_seconds=15,
            usage=(),
        )
        assert cost.billed_seconds == 15
        assert cost.platform_fee_paise == 75
        assert cost.platform_fee_waived is False
