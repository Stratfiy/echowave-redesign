"""Picking a template asks one more question: does it sound male or female?

It is the first thing anybody wants after choosing a template, and it has a
trap in it. A managed slot must not store "anushka" — that is a Sarvam name,
and storing one is the vendor pin `agent_templates` deliberately refuses to
make for models, because it stops the tier being free to move.

So the choice is stored as a gender and resolved to a real speaker at pipeline
build, against whichever vendor the tier is on that day. These cover both ends
of that: the sentinel survives being saved, and it turns into a voice that
actually exists.
"""

from __future__ import annotations

from api.schemas.ai_model_configuration import (
    DECIBYL_DEFAULT_VOICE,
    DECIBYL_GENDER_VOICES,
    DECIBYL_VOICE_FEMALE,
    DECIBYL_VOICE_MALE,
)
from api.services.configuration import voice_catalogue
from api.services.configuration.agent_options import managed_stack_override
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
    compile_workflow_model_configuration_override,
)
from api.services.configuration.registry import ServiceProviders


class TestTheSentinels:
    def test_they_are_not_the_default(self):
        assert DECIBYL_DEFAULT_VOICE not in DECIBYL_GENDER_VOICES

    def test_both_genders_are_offered(self):
        assert set(DECIBYL_GENDER_VOICES) == {DECIBYL_VOICE_MALE, DECIBYL_VOICE_FEMALE}

    def test_they_are_not_real_sarvam_voices(self):
        """The whole point. A sentinel that collided with a speaker name would
        resolve to that speaker and silently stop being a preference."""
        catalogue = voice_catalogue.for_provider(ServiceProviders.SARVAM.value)
        names = {v.voice_id for v in catalogue.voices}
        assert not (set(DECIBYL_GENDER_VOICES) & names)


class TestResolvingToASpeaker:
    def test_a_male_voice_resolves_to_a_male_speaker(self):
        voice_id = voice_catalogue.default_voice_id(
            ServiceProviders.SARVAM.value, model="bulbul:v2", gender="male"
        )
        assert voice_id is not None
        catalogue = voice_catalogue.for_provider(
            ServiceProviders.SARVAM.value, model="bulbul:v2"
        )
        chosen = next(v for v in catalogue.voices if v.voice_id == voice_id)
        assert chosen.gender == "male"

    def test_a_female_voice_resolves_to_a_female_speaker(self):
        voice_id = voice_catalogue.default_voice_id(
            ServiceProviders.SARVAM.value, model="bulbul:v2", gender="female"
        )
        catalogue = voice_catalogue.for_provider(
            ServiceProviders.SARVAM.value, model="bulbul:v2"
        )
        chosen = next(v for v in catalogue.voices if v.voice_id == voice_id)
        assert chosen.gender == "female"

    def test_it_resolves_on_the_newer_model_too(self):
        """v3 has its own speaker names; a v2 name is rejected at call time."""
        for gender in DECIBYL_GENDER_VOICES:
            v3 = voice_catalogue.default_voice_id(
                ServiceProviders.SARVAM.value, model="bulbul:v3", gender=gender
            )
            v2 = voice_catalogue.default_voice_id(
                ServiceProviders.SARVAM.value, model="bulbul:v2", gender=gender
            )
            assert v3 is not None and v2 is not None
            names_v3 = {
                v.voice_id
                for v in voice_catalogue.for_provider(
                    ServiceProviders.SARVAM.value, model="bulbul:v3"
                ).voices
            }
            assert v3 in names_v3

    def test_a_language_preference_does_not_break_the_answer(self):
        """Bulbul is multilingual — one speaker serves every Indic language, so
        the language is a label and must not filter the list to nothing."""
        assert (
            voice_catalogue.default_voice_id(
                ServiceProviders.SARVAM.value,
                model="bulbul:v2",
                gender="male",
                language="ta-IN",
            )
            is not None
        )

    def test_an_unknown_provider_resolves_to_nothing(self):
        """Better the vendor's own default than a guess in the wrong voice."""
        assert (
            voice_catalogue.default_voice_id(
                "a-vendor-we-do-not-publish", gender="male"
            )
            is None
        )

    def test_an_unknown_gender_resolves_to_nothing(self):
        assert (
            voice_catalogue.default_voice_id(
                ServiceProviders.SARVAM.value, model="bulbul:v2", gender="nonbinary"
            )
            is None
        )


class TestTheOverrideItWrites:
    """A template's voice choice must not quietly change anything else.

    An agent-level override is a *whole* stack — there is no way to say "the
    account's setup but a different voice", because every slot compiles
    together. So the account's own tiers have to be carried forward. Writing
    the defaults instead would move an account that had chosen the accurate
    brain back down to the standard one every time somebody started from a
    template, and nothing would have said so.
    """

    def _stack(self, override: dict) -> dict:
        return override[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY]["stack"]

    def test_the_voice_lands_on_the_tts_slot(self):
        stack = self._stack(managed_stack_override(voice="female", llm_tier="default"))
        assert stack["tts"]["voice"] == "female"

    def test_the_accounts_brain_tier_survives(self):
        stack = self._stack(managed_stack_override(voice="male", llm_tier="accurate"))
        assert stack["llm"]["model"] == "accurate"

    def test_the_accounts_speech_tiers_survive(self):
        stack = self._stack(
            managed_stack_override(
                voice="male", llm_tier="default", stt_tier="premium", tts_tier="premium"
            )
        )
        assert stack["stt"]["model"] == "premium"
        assert stack["tts"]["model"] == "premium"

    def test_every_slot_still_names_the_managed_tier(self):
        """A slot naming a real vendor is the pin this whole design avoids."""
        stack = self._stack(managed_stack_override(voice="female", llm_tier="default"))
        for slot in ("llm", "stt", "tts"):
            assert stack[slot]["provider"] == ServiceProviders.DECIBYL.value

    def test_it_compiles_and_the_voice_survives(self):
        """The override is stored as JSON and compiled at call time. A shape
        that stores fine and compiles to something else is the failure this
        catches."""
        override = managed_stack_override(voice="female", llm_tier="accurate")
        effective = compile_workflow_model_configuration_override(
            override[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY]
        )
        assert effective.tts.voice == "female"
        assert effective.llm.model == "accurate"

    def test_a_realtime_account_has_no_voice_slot(self):
        """Why the route declines to write one at all for those accounts: the
        choice would be recorded and then discarded, so the customer would see
        a voice on the agent and hear a different one on the call."""
        stack = self._stack(
            managed_stack_override(
                voice="female", llm_tier="default", realtime_tier="premium"
            )
        )
        assert "tts" not in stack
