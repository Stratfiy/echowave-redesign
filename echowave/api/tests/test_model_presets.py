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


def _cascade(llm_tier: str) -> SimpleNamespace:
    return SimpleNamespace(is_realtime=False, llm=_managed(llm_tier))


def _realtime(tier: str) -> SimpleNamespace:
    return SimpleNamespace(
        is_realtime=True, realtime=_managed(tier), llm=_managed(tier)
    )


class TestMatching:
    def test_each_cascade_preset_matches_its_own_tier(self):
        assert model_presets.match(_cascade("lite")) == "cost_saver"
        assert model_presets.match(_cascade("default")) == "balanced"
        assert model_presets.match(_cascade("accurate")) == "high_intelligence"

    def test_a_realtime_stack_matches_the_realtime_preset(self):
        assert model_presets.match(_realtime("natural")) == "ultra_fast"

    def test_a_realtime_stack_never_matches_a_cascade_preset(self):
        """The two describe different agents; "natural" is not a brain tier."""
        assert model_presets.match(_realtime("premium")) == model_presets.CUSTOM

    def test_a_vendor_named_stack_is_custom(self):
        """A hand-built stack is not any preset, however close it looks."""
        hand_built = SimpleNamespace(
            is_realtime=False, llm=SimpleNamespace(provider="openai", model="gpt-4.1")
        )
        assert model_presets.match(hand_built) == model_presets.CUSTOM

    def test_an_unknown_tier_is_custom(self):
        assert model_presets.match(_cascade("nonesuch")) == model_presets.CUSTOM

    def test_a_retired_tier_matches_the_preset_it_still_runs(self):
        """``zen`` and ``fast`` resolve to ``lite`` at dial time.

        An agent stored on one runs exactly the Cost Saver stack, so reading it
        as custom would be the chip disagreeing with the call.
        """
        assert model_presets.match(_cascade("zen")) == "cost_saver"
        assert model_presets.match(_cascade("fast")) == "cost_saver"

    def test_a_missing_section_is_custom_rather_than_a_crash(self):
        assert model_presets.match(SimpleNamespace(is_realtime=False)) == (
            model_presets.CUSTOM
        )


class TestAvailability:
    def test_a_cascade_preset_needs_ears_a_brain_and_a_voice(self):
        preset = model_presets.PRESETS_BY_SLUG["balanced"]
        full = {
            "llm": {"default": True},
            "stt": {"default": True},
            "tts": {"default": True},
        }
        assert model_presets.is_available(preset, full) is True

        # One missing key is one silent failure, so any of the three withholds
        # the whole preset.
        for missing in ("llm", "stt", "tts"):
            partial = {k: dict(v) for k, v in full.items()}
            partial[missing] = {"default": False}
            assert model_presets.is_available(preset, partial) is False, missing

    def test_a_realtime_preset_asks_only_about_realtime(self):
        preset = model_presets.PRESETS_BY_SLUG["ultra_fast"]
        assert model_presets.is_available(preset, {"realtime": {"natural": True}})
        assert not model_presets.is_available(preset, {"realtime": {"natural": False}})
        # No transcriber or voice to hold a key for -- one model does both.
        assert model_presets.is_available(preset, {"realtime": {"natural": True}})

    def test_nothing_keyed_offers_nothing(self):
        for preset in model_presets.MODEL_PRESETS:
            assert model_presets.is_available(preset, {}) is False


class TestTheSetItself:
    def test_every_preset_names_exactly_one_architecture(self):
        """Both set would describe an agent that cannot exist."""
        for preset in model_presets.MODEL_PRESETS:
            assert bool(preset.realtime_tier) != bool(preset.llm_tier), preset.slug

    def test_slugs_and_labels_are_distinct(self):
        slugs = [p.slug for p in model_presets.MODEL_PRESETS]
        labels = [p.label for p in model_presets.MODEL_PRESETS]
        assert len(set(slugs)) == len(slugs)
        assert len(set(labels)) == len(labels)

    def test_every_preset_names_a_tier_that_exists(self):
        """A preset pointing at a retired tier resolves to something else."""
        from api.services.configuration import managed_tiers

        for preset in model_presets.MODEL_PRESETS:
            if preset.realtime_tier:
                assert preset.realtime_tier in managed_tiers.REALTIME_TIERS, preset.slug
            else:
                assert preset.llm_tier in managed_tiers.LLM_TIERS, preset.slug


class TestWhatAPresetIsAllowedToChange:
    """The matcher and the writer have to agree on scope.

    The matcher reads only the brain for a cascade preset, so the writer must
    only write the brain. When they disagreed, an agent on ElevenLabs read as
    "Balanced" -- true by the matcher's rule -- and clicking Balanced then
    replaced its voice with managed speech. The label had been describing a
    stack the agent did not have, and the click made it true by force.
    """

    def _pipeline_stack(self, *, llm_tier: str) -> dict:
        return {
            "architecture": "pipeline",
            "llm": {"provider": "decibyl", "model": llm_tier, "api_key": ""},
            "stt": {"provider": "decibyl", "model": "default", "api_key": ""},
            # A deliberately chosen, non-managed voice -- the case that broke.
            "tts": {
                "provider": "elevenlabs",
                "model": "eleven_flash_v2_5",
                "voice": "some-elevenlabs-voice-id",
            },
        }

    def test_the_matcher_reads_only_the_brain(self):
        """So an ElevenLabs voice does not stop a preset matching..."""
        stack = self._pipeline_stack(llm_tier="default")
        effective = SimpleNamespace(
            is_realtime=False,
            llm=SimpleNamespace(**{k: v for k, v in stack["llm"].items()}),
        )
        assert model_presets.match(effective) == "balanced"

    def test_a_cascade_preset_touches_no_slot_but_the_brain(self):
        """...and so switching preset must leave that voice alone.

        Mirrors the route's merge: everything but ``llm`` is carried through
        byte for byte.
        """
        before = self._pipeline_stack(llm_tier="default")
        target = model_presets.PRESETS_BY_SLUG["high_intelligence"]

        after = dict(before)
        after["llm"] = {
            **dict(before["llm"]),
            "provider": "decibyl",
            "model": target.llm_tier,
            "api_key": "",
        }

        assert after["llm"]["model"] == "accurate"
        assert after["tts"] == before["tts"], "the chosen voice must survive"
        assert after["stt"] == before["stt"]
        assert after["architecture"] == "pipeline"

    def test_the_realtime_preset_is_the_one_that_may_replace_everything(self):
        """One model hears and speaks, so there is no voice left to keep."""
        preset = model_presets.PRESETS_BY_SLUG["ultra_fast"]
        assert preset.realtime_tier and not preset.llm_tier


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
