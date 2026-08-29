"""A provider failing mid-call should cost a pause, not the call.

A voice or transcriber that dies leaves the caller listening to silence, which
is the worst thing this product can hand them. Pipecat's
``ServiceSwitcherStrategyFailover`` moves to the next service when the active
one reports a non-fatal error; these tests cover the parts we own -- turning a
workflow's backup specs into real configuration sections, resolving their keys
through the same path as the primary, and only paying for a switcher when there
is something to switch to.
"""

import asyncio

import pytest

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.schemas.workflow_configurations import WorkflowConfigurationDefaults
from api.services.configuration import byok_resolution
from api.services.configuration.ai_model_configuration import _attach_backups


def _with_backups(**specs) -> EffectiveAIModelConfiguration:
    effective = EffectiveAIModelConfiguration()
    _attach_backups(effective, specs)
    return effective


def _provider(section) -> str:
    value = section.provider
    return value.value if hasattr(value, "value") else value


class TestBackupsBecomeRealConfigurationSections:
    def test_a_spec_builds_the_provider_s_own_configuration_class(self):
        """Not a lighter shape. Being the same kind of object as the primary is
        what lets a backup resolve a key, carry billing attribution and be
        built by the same factory branch."""
        effective = _with_backups(
            fallback_tts=[{"provider": "elevenlabs", "model": "eleven_flash_v2_5"}]
        )

        assert len(effective.fallback_tts) == 1
        assert _provider(effective.fallback_tts[0]) == "elevenlabs"
        assert effective.fallback_tts[0].model == "eleven_flash_v2_5"

    def test_unspecified_fields_take_the_provider_s_defaults(self):
        """A backup is chosen to keep a call alive, not to be tuned."""
        effective = _with_backups(fallback_tts=[{"provider": "cartesia"}])

        assert effective.fallback_tts[0].model, "should have taken Cartesia's default"

    def test_order_is_preserved(self):
        effective = _with_backups(
            fallback_tts=[{"provider": "elevenlabs"}, {"provider": "cartesia"}]
        )

        assert [_provider(s) for s in effective.fallback_tts] == ["elevenlabs", "cartesia"]

    def test_transcriber_backups_build_too(self):
        effective = _with_backups(fallback_stt=[{"provider": "deepgram", "model": "nova-3"}])

        assert _provider(effective.fallback_stt[0]) == "deepgram"


class TestABadBackupNeverStopsTheCall:
    def test_a_managed_backup_is_refused(self):
        """Running a backup on Decibyl's key is a pricing decision, and nothing
        here asked the account to agree to one."""
        assert _with_backups(fallback_tts=[{"provider": "decibyl"}]).fallback_tts == []

    def test_an_unknown_provider_is_dropped_rather_than_raised(self):
        assert _with_backups(fallback_tts=[{"provider": "nonesuch"}]).fallback_tts == []

    def test_a_spec_with_no_provider_is_dropped(self):
        assert _with_backups(fallback_tts=[{"model": "x"}]).fallback_tts == []

    def test_no_backups_configured_is_the_ordinary_case(self):
        effective = _with_backups()

        assert effective.fallback_tts == []
        assert effective.fallback_stt == []


class TestBackupsGetKeysAndBillingLikeAPrimary:
    def test_the_key_resolver_walks_them(self):
        """Skipped, a backup would have no key -- silence at the one moment it
        exists for."""
        effective = _with_backups(fallback_tts=[{"provider": "elevenlabs"}])

        labels = [label for label, _c, _s in byok_resolution._all_sections(effective)]

        assert "fallback_tts[0]" in labels

    def test_each_backup_is_labelled_by_position(self):
        effective = _with_backups(
            fallback_stt=[{"provider": "deepgram"}, {"provider": "assemblyai"}]
        )

        labels = [label for label, _c, _s in byok_resolution._all_sections(effective)]

        assert "fallback_stt[0]" in labels and "fallback_stt[1]" in labels

    def test_a_backup_is_stamped_for_billing(self):
        """Unattributed usage on a backup is a margin figure that reads better
        than it is -- the same hole the pricing tests exist to close."""
        effective = _with_backups(fallback_tts=[{"provider": "elevenlabs"}])

        asyncio.run(byok_resolution.apply(effective, organization_id=None))

        assert effective.fallback_tts[0].key_source == "byok"

    def test_the_component_is_right_so_the_vault_is_asked_the_right_question(self):
        effective = _with_backups(
            fallback_tts=[{"provider": "elevenlabs"}],
            fallback_stt=[{"provider": "deepgram"}],
        )

        by_label = {
            label: component
            for label, component, _s in byok_resolution._all_sections(effective)
        }

        assert by_label["fallback_tts[0]"].value == "tts"
        assert by_label["fallback_stt[0]"].value == "stt"


class TestTheSchema:
    def test_backups_default_to_none_configured(self):
        configuration = WorkflowConfigurationDefaults()

        assert configuration.fallback_tts == []
        assert configuration.fallback_stt == []

    def test_a_provider_is_required(self):
        with pytest.raises(ValueError):
            WorkflowConfigurationDefaults(fallback_tts=[{"model": "x"}])

    def test_the_chain_is_capped(self):
        """Each backup is a live connection held open for a failure that
        usually never comes."""
        with pytest.raises(ValueError):
            WorkflowConfigurationDefaults(
                fallback_tts=[{"provider": "a"}, {"provider": "b"}, {"provider": "c"}]
            )


class TestOnlyPayForASwitcherWhenThereIsSomethingToSwitchTo:
    """A switcher around one service is a ParallelPipeline and a pair of
    filters on every frame, for a failover that can never happen."""

    @staticmethod
    def _factory():
        from api.services.pipecat.service_factory import _with_backups

        return _with_backups

    def test_no_backups_returns_the_primary_untouched(self):
        primary = object()

        assert self._factory()(primary, [], "TTS") is primary

    def test_backups_produce_a_failover_switcher(self):
        from pipecat.pipeline.service_switcher import (
            ServiceSwitcher,
            ServiceSwitcherStrategyFailover,
        )
        from pipecat.processors.frame_processor import FrameProcessor

        primary = FrameProcessor(name="primary")
        backup = FrameProcessor(name="backup")

        switcher = self._factory()(primary, [backup], "TTS")

        assert isinstance(switcher, ServiceSwitcher)
        assert isinstance(switcher.strategy, ServiceSwitcherStrategyFailover)
        assert switcher.services == [primary, backup]

    def test_the_primary_is_the_one_that_starts_active(self):
        from pipecat.processors.frame_processor import FrameProcessor

        primary = FrameProcessor(name="primary")
        switcher = self._factory()(primary, [FrameProcessor(name="backup")], "TTS")

        assert switcher.strategy.active_service is primary

    def test_a_non_fatal_error_from_the_active_service_moves_to_the_backup(self):
        """The whole point. A fatal error is left alone: the provider is saying
        the call cannot continue, and a second service will not change that."""
        from pipecat.frames.frames import ErrorFrame
        from pipecat.processors.frame_processor import FrameProcessor

        primary = FrameProcessor(name="primary")
        backup = FrameProcessor(name="backup")
        switcher = self._factory()(primary, [backup], "TTS")

        fatal = ErrorFrame(error="cannot continue", fatal=True)
        asyncio.run(switcher.push_frame(fatal))
        assert switcher.strategy.active_service is primary

        transient = ErrorFrame(error="bad minute")
        transient.processor = primary
        asyncio.run(switcher.push_frame(transient))
        assert switcher.strategy.active_service is backup
