"""A preset must describe the stack, or it is worse than no preset at all.

The failure this guards is quiet: an agent hand-tuned to something we do not
sell reads as "Balanced", the customer believes their models are one thing, and
the first sign otherwise is a bill or a bad call. So matching is strict, and
anything unrecognised is ``custom``.

Matched on **tiers**, never on the vendors a tier resolves to. Comparing
vendors would make every agent stop matching its own preset the day we move a
tier -- the exact coupling tiers exist to prevent -- so there is a test for
that specifically.
"""

from __future__ import annotations

from types import SimpleNamespace

from api.services.configuration import model_presets


def _managed(tier: str) -> SimpleNamespace:
    """A managed section: provider "decibyl", tier in `model`."""
    return SimpleNamespace(provider="decibyl", model=tier)


def _realtime(tier: str) -> SimpleNamespace:
    return SimpleNamespace(
        is_realtime=True, realtime=_managed(tier), llm=_managed(tier)
    )


def _cascade(llm_tier: str, *, stt_tier: str = "default", tts_tier: str = "default"):
    return SimpleNamespace(
        is_realtime=False,
        llm=_managed(llm_tier),
        stt=_managed(stt_tier),
        tts=_managed(tts_tier),
    )


class TestMatching:
    def test_each_preset_matches_its_own_tiers(self):
        assert model_presets.match(_cascade("lite", tts_tier="basic")) == "basic"
        assert model_presets.match(_cascade("lite")) == "standard"
        assert model_presets.match(_cascade("accurate")) == "smart"
        assert model_presets.match(_cascade("default", tts_tier="global")) == "global"

    def test_the_voice_is_part_of_the_match(self):
        """Smart on a Rumik voice is not Smart: a click on the chip would
        quietly move the voice back, so the row must say Customized."""
        assert model_presets.match(_cascade("accurate", tts_tier="basic")) == (
            model_presets.CUSTOM
        )

    def test_a_realtime_stack_matches_no_cascade_preset(self):
        assert model_presets.match(_realtime("natural")) == model_presets.CUSTOM

    def test_a_vendor_named_stack_is_custom(self):
        """A hand-built stack is not any preset, however close it looks."""
        hand_built = SimpleNamespace(
            is_realtime=False,
            llm=SimpleNamespace(provider="openai", model="gpt-4.1"),
            stt=_managed("default"),
            tts=_managed("default"),
        )
        assert model_presets.match(hand_built) == model_presets.CUSTOM

    def test_an_unknown_tier_is_custom(self):
        assert model_presets.match(_cascade("nonesuch")) == model_presets.CUSTOM

    def test_a_retired_tier_matches_the_preset_it_still_runs(self):
        """``zen`` and ``fast`` resolve to ``lite`` at dial time.

        An agent stored on one runs exactly the Standard stack, so reading it
        as custom would be the chip disagreeing with the call.
        """
        assert model_presets.match(_cascade("zen")) == "standard"
        assert model_presets.match(_cascade("fast")) == "standard"

    def test_a_missing_section_is_custom_rather_than_a_crash(self):
        assert model_presets.match(SimpleNamespace(is_realtime=False)) == (
            model_presets.CUSTOM
        )
        assert (
            model_presets.match(
                SimpleNamespace(is_realtime=False, llm=_managed("lite"))
            )
            == model_presets.CUSTOM
        )


class TestAvailability:
    def test_a_preset_needs_ears_a_brain_and_its_own_voice_tier(self):
        preset = model_presets.PRESETS_BY_SLUG["basic"]
        full = {
            "llm": {"lite": True},
            "stt": {"default": True},
            "tts": {"basic": True, "default": True},
        }
        assert model_presets.is_available(preset, full) is True

        # One missing key is one silent failure, so any of the three withholds
        # the whole preset -- and it is *this* preset's voice tier that counts.
        no_rumik = {**full, "tts": {"basic": False, "default": True}}
        assert model_presets.is_available(preset, no_rumik) is False
        for missing in ("llm", "stt"):
            partial = {k: dict(v) for k, v in full.items()}
            partial[missing] = {t: False for t in partial[missing]}
            assert model_presets.is_available(preset, partial) is False, missing

    def test_nothing_keyed_offers_nothing(self):
        for preset in model_presets.MODEL_PRESETS:
            assert model_presets.is_available(preset, {}) is False


class TestTheSetItself:
    def test_the_ladder_is_the_four_rungs_in_price_order(self):
        assert [p.slug for p in model_presets.MODEL_PRESETS] == [
            "basic",
            "standard",
            "smart",
            "global",
        ]

    def test_every_preset_names_exactly_one_architecture(self):
        """Both set would describe an agent that cannot exist."""
        for preset in model_presets.MODEL_PRESETS:
            assert bool(preset.realtime_tier) != bool(preset.llm_tier), preset.slug

    def test_slugs_and_labels_are_distinct(self):
        slugs = [p.slug for p in model_presets.MODEL_PRESETS]
        labels = [p.label for p in model_presets.MODEL_PRESETS]
        assert len(set(slugs)) == len(slugs)
        assert len(set(labels)) == len(labels)

    def test_every_preset_names_tiers_that_exist(self):
        """A preset pointing at a retired tier resolves to something else."""
        from api.services.configuration import managed_tiers

        for preset in model_presets.MODEL_PRESETS:
            if preset.realtime_tier:
                assert preset.realtime_tier in managed_tiers.REALTIME_TIERS, preset.slug
            else:
                assert preset.llm_tier in managed_tiers.LLM_TIERS, preset.slug
                assert preset.stt_tier in managed_tiers.STT_TIERS, preset.slug
                assert preset.tts_tier in managed_tiers.TTS_TIERS, preset.slug

    def test_the_voice_tiers_resolve_to_vendors_with_a_published_catalogue(self):
        """A preset whose voice tier lists no voices is a picker with nothing
        to play, which is the state the bundles left the product in."""
        from api.services.configuration import voice_catalogue

        for preset in model_presets.MODEL_PRESETS:
            if preset.realtime_tier:
                continue
            catalogue = voice_catalogue.for_provider("decibyl", model=preset.tts_tier)
            assert catalogue.voices, preset.slug


class TestRecommending:
    """The cheapest rung that does the job, and the reason in one sentence."""

    def _pick(self, **kw):
        return model_presets.recommend(model_presets.Requirement(**kw))

    def test_hindi_and_english_alone_is_basic(self):
        assert self._pick(languages=("Hindi", "English")).slug == "basic"
        assert self._pick(languages=("hi", "en-IN")).slug == "basic"

    def test_any_other_indian_language_needs_standard(self):
        assert self._pick(languages=("Hindi", "Tamil")).slug == "standard"
        assert self._pick(languages=("te",)).slug == "standard"

    def test_unknown_languages_are_read_as_indian_rather_than_hindi(self):
        """An agent that named none is not thereby a Hindi-only agent."""
        assert self._pick().slug == "standard"

    def test_tools_or_documents_need_the_stronger_brain(self):
        assert self._pick(languages=("Hindi",), uses_tools=True).slug == "smart"

    def test_a_language_from_outside_india_is_global(self):
        assert self._pick(languages=("English", "fr")).slug == "global"

    def test_every_recommendation_says_why(self):
        for languages in ((), ("hi",), ("ta",), ("de",)):
            pick = self._pick(languages=languages)
            assert pick.reason.endswith(".")

    def test_an_unavailable_rung_is_skipped_upward(self):
        pick = model_presets.recommend(
            model_presets.Requirement(languages=("hi",)),
            available={"basic": False, "standard": True, "smart": True, "global": True},
        )
        assert pick.slug == "standard"
        assert "not available" in pick.reason

    def test_nothing_available_still_names_the_right_rung(self):
        pick = model_presets.recommend(
            model_presets.Requirement(languages=("hi",)),
            available={p.slug: False for p in model_presets.MODEL_PRESETS},
        )
        assert pick.slug == "basic"


class TestOneSlotAtATime:
    """The Advanced tiles' pencil changes one slot and nothing else."""

    def _cascade_stack(self):
        return {
            "architecture": "pipeline",
            "stt": {"provider": "decibyl", "model": "default", "api_key": ""},
            "llm": {"provider": "decibyl", "model": "accurate", "api_key": ""},
            "tts": {
                "provider": "sarvam",
                "model": "bulbul:v2",
                "voice": "anushka",
                "api_key": "",
                "use_platform_key": True,
            },
        }

    def test_changing_the_voice_leaves_the_brain_alone(self):
        from api.services.configuration.agent_options import with_model_slot

        out = with_model_slot(
            self._cascade_stack(),
            component="tts",
            provider="sarvam",
            model="bulbul:v3",
            voice="manisha",
        )
        assert out["llm"] == {"provider": "decibyl", "model": "accurate", "api_key": ""}
        assert out["tts"]["model"] == "bulbul:v3"
        assert out["tts"]["voice"] == "manisha"
        assert out["tts"]["use_platform_key"] is True

    def test_a_new_vendor_does_not_inherit_the_old_vendors_voice(self):
        from api.services.configuration.agent_options import with_model_slot

        out = with_model_slot(
            self._cascade_stack(),
            component="tts",
            provider="elevenlabs",
            model="eleven_flash_v2_5",
        )
        assert "voice" not in out["tts"]

    def test_the_input_is_not_mutated(self):
        from api.services.configuration.agent_options import with_model_slot

        stack = self._cascade_stack()
        with_model_slot(
            stack, component="llm", provider="google", model="gemini-2.5-flash"
        )
        assert stack["llm"]["provider"] == "decibyl"

    def test_a_speech_to_speech_model_replaces_ears_and_voice(self):
        from api.services.configuration.agent_options import with_model_slot

        out = with_model_slot(
            self._cascade_stack(),
            component="realtime",
            provider="openai_realtime",
            model="gpt-realtime",
        )
        assert out["architecture"] == "realtime"
        assert "stt" not in out and "tts" not in out
        assert out["realtime"]["model"] == "gpt-realtime"
        assert "llm" in out

    def test_picking_a_cascade_slot_leaves_speech_to_speech(self):
        from api.services.configuration.agent_options import with_model_slot

        realtime = {
            "architecture": "realtime",
            "realtime": {
                "provider": "openai_realtime",
                "model": "gpt-realtime",
                "api_key": "",
                "use_platform_key": True,
            },
            "llm": {"provider": "decibyl", "model": "default", "api_key": ""},
        }
        out = with_model_slot(
            realtime, component="llm", provider="google", model="gemini-2.5-flash"
        )
        assert out["architecture"] == "pipeline"
        assert "realtime" not in out
        assert (
            out["stt"]["provider"] == "decibyl" and out["tts"]["provider"] == "decibyl"
        )

    def test_an_unknown_slot_is_refused(self):
        import pytest

        from api.services.configuration.agent_options import (
            SelectionError,
            with_model_slot,
        )

        with pytest.raises(SelectionError):
            with_model_slot(
                self._cascade_stack(), component="embeddings", provider="x", model="y"
            )

    def test_a_managed_configuration_dumps_to_a_stack_without_keys(self):
        from api.services.configuration.agent_options import (
            managed_stack_override,
            stack_from_configurations,
        )
        from api.services.configuration.ai_model_configuration import (
            compile_workflow_model_configuration_override,
        )

        override = managed_stack_override(voice="female", llm_tier="accurate")
        effective = compile_workflow_model_configuration_override(
            override["model_configuration_v2_override"]
        )
        stack = stack_from_configurations(effective)
        assert stack["architecture"] == "pipeline"
        assert stack["llm"]["provider"] == "decibyl"
        assert all(
            not section.get("api_key")
            for name, section in stack.items()
            if isinstance(section, dict)
        )


class TestTheKnobsBehindThePencil:
    """The panel behind a tile tunes the slot it is on and no other."""

    def _stack(self):
        return {
            "architecture": "pipeline",
            "stt": {"provider": "decibyl", "model": "default", "api_key": ""},
            "llm": {
                "provider": "openai",
                "model": "gpt-4.1",
                "api_key": "",
                "use_platform_key": True,
                "temperature": 0.7,
            },
            "tts": {
                "provider": "sarvam",
                "model": "bulbul:v3",
                "voice": "anushka",
                "api_key": "",
                "use_platform_key": True,
                "speed": 1.0,
                "language": "hi-IN",
            },
        }

    def test_a_value_given_is_written(self):
        from api.services.configuration.agent_options import with_model_slot

        out = with_model_slot(
            self._stack(),
            component="llm",
            provider="openai",
            model="gpt-4.1",
            tuning={"temperature": 0.2, "max_tokens": 200},
        )
        assert out["llm"]["temperature"] == 0.2
        assert out["llm"]["max_tokens"] == 200

    def test_a_value_left_out_keeps_what_the_slot_had(self):
        from api.services.configuration.agent_options import with_model_slot

        out = with_model_slot(
            self._stack(),
            component="tts",
            provider="sarvam",
            model="bulbul:v3",
            tuning={"speed": 1.3, "language": None},
        )
        assert out["tts"]["speed"] == 1.3
        assert out["tts"]["language"] == "hi-IN"
        assert out["tts"]["voice"] == "anushka"

    def test_a_knob_the_slot_does_not_have_is_not_stored(self):
        from api.services.configuration.agent_options import with_model_slot

        out = with_model_slot(
            self._stack(),
            component="stt",
            provider="decibyl",
            model="default",
            # Temperature is a brain's knob; on the ears it means nothing.
            tuning={"temperature": 0.9, "language": "ta-IN"},
        )
        assert "temperature" not in out["stt"]
        assert out["stt"]["language"] == "ta-IN"

    def test_the_brain_is_untouched_by_the_voices_knobs(self):
        from api.services.configuration.agent_options import with_model_slot

        out = with_model_slot(
            self._stack(),
            component="tts",
            provider="sarvam",
            model="bulbul:v3",
            tuning={"speed": 0.8},
        )
        assert out["llm"] == self._stack()["llm"]

    def test_the_row_reports_what_a_section_carries_and_nothing_it_lacks(self):
        from api.services.configuration.agent_options import slot_tuning

        llm = SimpleNamespace(temperature=0.4, max_tokens=None)
        assert slot_tuning("llm", llm) == {"temperature": 0.4}
        tts = SimpleNamespace(speed=1.2, language="en-IN", voice="x")
        assert slot_tuning("tts", tts) == {"speed": 1.2, "language": "en-IN"}
        assert slot_tuning("realtime", SimpleNamespace(voice="alloy")) == {}
        assert slot_tuning("llm", None) == {}
