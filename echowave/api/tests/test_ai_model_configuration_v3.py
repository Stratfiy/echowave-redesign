"""One stack, each slot independently managed or BYOK.

v2 made managed-versus-BYOK an account-wide switch, and then folded a second,
unrelated choice into the same field: cascade pipeline versus speech-to-speech.
The UI could only render that as three exclusive tabs, two of which were about
key ownership and one about architecture, which is why the screen read as three
alternatives that were not comparable things.

The tests here pin the two properties that separation buys, because both were
impossible before and neither is obvious from the type definitions:

* **A mixed stack is representable.** Managed Indic STT alongside the
  customer's own OpenAI key is the case the Indian market actually wants, and
  v2 rejected it outright.
* **Managed-ness is derived from the slots, never stored.** There is no `mode`
  field to drift out of agreement with what the slots say.
"""

import pytest
from pydantic import ValidationError

from api.schemas.ai_model_configuration import (
    AIModelStackV3,
    OrganizationAIModelConfigurationV2,
    OrganizationAIModelConfigurationV3,
    compile_ai_model_configuration_v3,
    upgrade_v2_to_v3,
)

MANAGED = {"provider": "decibyl", "model": "default"}


def _stack(**overrides):
    base = {
        "architecture": "pipeline",
        "llm": {"provider": "openai", "model": "gpt-4o", "api_key": "sk-test"},
        "stt": {"provider": "deepgram", "model": "nova-2", "api_key": "dg-test"},
        "tts": {
            "provider": "elevenlabs",
            "model": "eleven_turbo_v2",
            "api_key": "el-test",
        },
    }
    base.update(overrides)
    return AIModelStackV3.model_validate(base)


class TestAMixedStack:
    """The configuration v2 could not express at all."""

    def test_managed_stt_alongside_a_byok_llm_is_valid(self):
        stack = _stack(stt=MANAGED)
        assert stack.managed_slots() == ["stt"]
        assert "llm" in stack.byok_slots()

    def test_every_slot_can_be_managed(self):
        stack = _stack(stt=MANAGED, llm=MANAGED, tts=MANAGED)
        assert set(stack.managed_slots()) == {"stt", "llm", "tts"}
        assert stack.byok_slots() == []

    def test_every_slot_can_be_byok(self):
        stack = _stack()
        assert stack.managed_slots() == []
        assert set(stack.byok_slots()) == {"stt", "llm", "tts"}

    def test_the_india_case_managed_indic_speech_with_your_own_llm(self):
        """The specific stack the market wants: our Indic STT and TTS, which
        beat the Western equivalents on Telugu, with the customer's own
        negotiated OpenAI contract for the reasoning."""
        stack = _stack(stt=MANAGED, tts=MANAGED)
        assert set(stack.managed_slots()) == {"stt", "tts"}
        assert stack.byok_slots() == ["llm"]

    def test_managed_speech_to_speech_is_expressible(self):
        """Also impossible in v2, where realtime lived under the BYOK branch
        and therefore could never run on our keys."""
        stack = _stack(
            architecture="realtime",
            realtime=MANAGED,
            llm=MANAGED,
            stt=None,
            tts=None,
        )
        assert "realtime" in stack.managed_slots()


class TestThereIsNoModeField:
    def test_managed_ness_is_derived_from_the_slots(self):
        """Storing a summary of the slots would let it drift out of agreement
        with them, and the summary is what people would then trust."""
        assert not hasattr(AIModelStackV3, "mode")
        stack = _stack(stt=MANAGED)
        assert stack.managed_slots() == ["stt"]

    def test_changing_a_slot_changes_what_is_managed(self):
        assert _stack().managed_slots() == []
        assert _stack(llm=MANAGED).managed_slots() == ["llm"]


class TestArchitectureIsItsOwnAxis:
    def test_a_pipeline_needs_a_transcriber_and_a_voice(self):
        with pytest.raises(ValidationError, match="stt and tts"):
            AIModelStackV3.model_validate(
                {
                    "architecture": "pipeline",
                    "llm": {"provider": "openai", "model": "gpt-4o", "api_key": "sk"},
                }
            )

    def test_the_error_offers_the_other_architecture(self):
        """A validation message that only says what is missing leaves someone
        adding a transcriber they did not want."""
        with pytest.raises(ValidationError, match="speech-to-speech"):
            AIModelStackV3.model_validate(
                {
                    "architecture": "pipeline",
                    "llm": {"provider": "openai", "model": "gpt-4o", "api_key": "sk"},
                }
            )

    def test_speech_to_speech_needs_a_realtime_model(self):
        with pytest.raises(ValidationError, match="realtime model"):
            AIModelStackV3.model_validate(
                {
                    "architecture": "realtime",
                    "llm": {"provider": "openai", "model": "gpt-4o", "api_key": "sk"},
                }
            )

    def test_speech_to_speech_still_requires_an_llm(self):
        """The realtime model carries the conversation; the LLM does variable
        extraction and QA. Asking the realtime model for structured extraction
        mid-call is not the same job."""
        with pytest.raises(ValidationError):
            AIModelStackV3.model_validate(
                {
                    "architecture": "realtime",
                    "realtime": {
                        "provider": "openai_realtime",
                        "model": "x",
                        "api_key": "sk",
                    },
                }
            )


class TestCompilingToWhatThePipelineReads:
    def test_a_pipeline_stack_compiles_with_all_three_sections(self):
        effective = compile_ai_model_configuration_v3(
            OrganizationAIModelConfigurationV3(stack=_stack())
        )
        assert effective.is_realtime is False
        assert effective.stt is not None and effective.tts is not None

    def test_a_realtime_stack_drops_the_transcriber_and_voice(self):
        """A speech-to-speech model replaces both. Passing them through would
        have the factory build services nothing consumes."""
        effective = compile_ai_model_configuration_v3(
            OrganizationAIModelConfigurationV3(
                stack=_stack(
                    architecture="realtime",
                    realtime={
                        "provider": "openai_realtime",
                        "model": "x",
                        "api_key": "sk",
                    },
                )
            )
        )
        assert effective.is_realtime is True
        assert effective.stt is None and effective.tts is None
        assert effective.realtime is not None

    def test_managed_slots_are_left_saying_decibyl(self):
        """Resolution to a real vendor happens later, in managed_resolution,
        which is the only place that should know how a tier maps to a provider."""
        effective = compile_ai_model_configuration_v3(
            OrganizationAIModelConfigurationV3(stack=_stack(stt=MANAGED))
        )
        assert effective.stt.provider.value == "decibyl"


class TestReadingAStoredV2Configuration:
    """Upgrading on read rather than migrating the data.

    A migration would rewrite every account's configuration in one step, and a
    mistake there is an outage on the next call for everyone. Reading the old
    shape costs nothing and leaves the row alone until the customer next saves.
    """

    def test_a_managed_v2_configuration_becomes_managed_slots(self):
        v2 = OrganizationAIModelConfigurationV2.model_validate(
            {
                "version": 2,
                "mode": "decibyl",
                "decibyl": {"voice": "default", "speed": 1.0, "language": "te-IN"},
            }
        )
        v3 = upgrade_v2_to_v3(v2)
        assert v3.stack.architecture == "pipeline"
        assert set(v3.stack.managed_slots()) >= {"stt", "llm", "tts"}

    def test_a_byok_pipeline_v2_configuration_keeps_its_providers(self):
        v2 = OrganizationAIModelConfigurationV2.model_validate(
            {
                "version": 2,
                "mode": "byok",
                "byok": {
                    "mode": "pipeline",
                    "pipeline": {
                        "llm": {
                            "provider": "openai",
                            "model": "gpt-4o",
                            "api_key": "sk",
                        },
                        "stt": {
                            "provider": "deepgram",
                            "model": "nova-2",
                            "api_key": "dg",
                        },
                        "tts": {
                            "provider": "elevenlabs",
                            "model": "eleven_turbo_v2",
                            "api_key": "el",
                        },
                    },
                },
            }
        )
        v3 = upgrade_v2_to_v3(v2)
        assert v3.stack.architecture == "pipeline"
        assert v3.stack.llm.provider.value == "openai"
        assert v3.stack.managed_slots() == []

    def test_a_byok_realtime_v2_configuration_becomes_a_realtime_stack(self):
        v2 = OrganizationAIModelConfigurationV2.model_validate(
            {
                "version": 2,
                "mode": "byok",
                "byok": {
                    "mode": "realtime",
                    "realtime": {
                        "realtime": {
                            "provider": "openai_realtime",
                            "model": "x",
                            "api_key": "sk",
                        },
                        "llm": {
                            "provider": "openai",
                            "model": "gpt-4o",
                            "api_key": "sk",
                        },
                    },
                },
            }
        )
        v3 = upgrade_v2_to_v3(v2)
        assert v3.stack.architecture == "realtime"
        assert v3.stack.realtime is not None


class TestV2NoLongerRejectsAMixedStack:
    """The one rule whose removal is the entire redesign.

    Nothing downstream ever needed it: the config unions already admitted the
    Decibyl variants and managed_resolution already rewrote each section
    independently. It existed only because ``mode`` claimed the account was one
    thing or the other.
    """

    def test_a_v2_byok_pipeline_may_now_name_decibyl_in_one_slot(self):
        configuration = OrganizationAIModelConfigurationV2.model_validate(
            {
                "version": 2,
                "mode": "byok",
                "byok": {
                    "mode": "pipeline",
                    "pipeline": {
                        "llm": {
                            "provider": "openai",
                            "model": "gpt-4o",
                            "api_key": "sk",
                        },
                        "stt": MANAGED,
                        "tts": {
                            "provider": "elevenlabs",
                            "model": "eleven_turbo_v2",
                            "api_key": "el",
                        },
                    },
                },
            }
        )
        v3 = upgrade_v2_to_v3(configuration)
        assert v3.stack.managed_slots() == ["stt"]
