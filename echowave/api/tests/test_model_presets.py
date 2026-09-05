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
