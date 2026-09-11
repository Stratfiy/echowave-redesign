"""The voice-sample generator must cover every Sarvam voice the picker shows.

v2 and v3 are different speaker sets (anushka/karun on v2, shubh/aditya on
v3) and an agent on either tier reaches the picker. Sampling one tier left
half the voices with no play button — the bug this guards against. The sample
path is keyed by voice id alone, so a name shared across tiers is recorded
once, under the first tier that lists it.
"""

from unittest.mock import AsyncMock

import pytest

from scripts.generate_voice_samples import (
    SAMPLE_MODELS,
    _sarvam_voices_to_sample,
)


def test_both_tiers_are_sampled():
    voices = _sarvam_voices_to_sample()
    models = {model for _, model in voices}
    assert models == set(SAMPLE_MODELS)
    # A v2-only and a v3-only name both appear.
    ids = {vid for vid, _ in voices}
    assert "karun" in ids  # v2
    assert "shubh" in ids  # v3


def test_a_voice_is_sampled_once():
    voices = _sarvam_voices_to_sample()
    ids = [vid for vid, _ in voices]
    assert len(ids) == len(set(ids)), "a voice id was queued for sampling twice"


class TestTheKeyItRecordsWith:
    """Where the vendor key comes from, and what happens when there isn't one.

    The file's instructions have always said it "uses the platform's own Sarvam
    key when one is stored, so a deployment does not need a second credential".
    It did not — both keys were read from the environment and nowhere else. On
    a deployment holding its Sarvam key in the vault, which is every managed
    one, the script found nothing, logged it at INFO and **exited 0**. The run
    looked clean, the samples were never recorded, and the picker showed no
    play buttons with nothing to indicate why. This deployment is in exactly
    that state.
    """

    @pytest.mark.asyncio
    async def test_the_environment_wins(self, monkeypatch):
        """Recording against a scratch key without touching the vault keeps
        working."""
        from scripts import generate_voice_samples as gen

        monkeypatch.setenv("SARVAM_API_KEY", "sk-from-the-environment")
        assert await gen._vendor_key("SARVAM_API_KEY", "sarvam") == (
            "sk-from-the-environment"
        )

    @pytest.mark.asyncio
    async def test_it_falls_back_to_the_platform_vault(self, monkeypatch):
        from api.services.configuration import platform_credentials
        from scripts import generate_voice_samples as gen

        monkeypatch.delenv("SARVAM_API_KEY", raising=False)

        class _Session:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        from api.db import db_client

        monkeypatch.setattr(db_client, "async_session", _Session)
        monkeypatch.setattr(
            platform_credentials,
            "resolve_api_key",
            AsyncMock(return_value="sk-from-the-vault"),
        )

        assert await gen._vendor_key("SARVAM_API_KEY", "sarvam") == "sk-from-the-vault"

    @pytest.mark.asyncio
    async def test_a_vault_that_cannot_be_read_is_not_a_crash(self, monkeypatch):
        """It is a script; a missing database should report no key, not a
        traceback that hides the ElevenLabs half of the run."""
        from api.db import db_client
        from scripts import generate_voice_samples as gen

        monkeypatch.delenv("SARVAM_API_KEY", raising=False)

        def _explode():
            raise RuntimeError("no database here")

        monkeypatch.setattr(db_client, "async_session", _explode)
        assert await gen._vendor_key("SARVAM_API_KEY", "sarvam") is None

    @pytest.mark.asyncio
    async def test_no_key_with_voices_to_sample_is_a_failure(self, monkeypatch):
        """The bug. Work to do, no way to do it, and it used to exit 0."""
        from scripts import generate_voice_samples as gen

        monkeypatch.setattr(gen, "_vendor_key", AsyncMock(return_value=None))
        monkeypatch.setattr(
            gen, "_elevenlabs_samples", AsyncMock(return_value=(0, 0, 0))
        )
        monkeypatch.setattr(
            gen, "_sarvam_voices_to_sample", lambda: [("anushka", "bulbul:v2")]
        )

        assert await gen.main(force=False) == 1

    @pytest.mark.asyncio
    async def test_no_key_and_nothing_to_sample_is_not_a_failure(self, monkeypatch):
        """Nothing was asked of it, so nothing went wrong."""
        from scripts import generate_voice_samples as gen

        monkeypatch.setattr(gen, "_vendor_key", AsyncMock(return_value=None))
        monkeypatch.setattr(
            gen, "_elevenlabs_samples", AsyncMock(return_value=(0, 0, 0))
        )
        monkeypatch.setattr(gen, "_sarvam_voices_to_sample", list)

        assert await gen.main(force=False) == 0


class TestTheRumikVoices:
    """Mulberry's preset speakers, in the two languages Rumik actually speaks.

    ``muga`` is the model the managed tier currently points at and it takes no
    preset speaker at all — it is directed by tone tags in the text — so there
    is nothing to name and nothing to sample there. ``mulberry`` is the one
    with voices, and the one the options module calls "the one that belongs on
    a phone call".
    """

    def test_it_samples_the_model_that_has_speakers(self):
        from scripts import generate_voice_samples as gen

        assert gen.RUMIK_SAMPLE_MODEL == "mulberry"

    def test_it_records_only_the_languages_rumik_speaks(self):
        """Rumik is "a cost win for Hindi/English agents and unusable for a
        Telugu one". Recording the Telugu line anyway would not fail — it would
        produce a confident sample of a model mispronouncing a language it does
        not know, which is worse than no sample."""
        from scripts import generate_voice_samples as gen

        languages = gen._rumik_languages()
        assert set(languages) == {"en", "hi"}
        for unsupported in ("ta", "kn", "te"):
            assert unsupported not in languages

    def test_every_language_it_records_has_a_line_to_say(self):
        from api.services.configuration import voice_samples
        from scripts import generate_voice_samples as gen

        for language in gen._rumik_languages():
            assert voice_samples.SAMPLE_LINES[language].strip()

    @pytest.mark.asyncio
    async def test_a_name_shared_with_sarvam_is_skipped_not_overwritten(
        self, monkeypatch
    ):
        """Sample paths are keyed by voice id alone, and so is the URL the
        picker builds, so two vendors sharing a name cannot both have a
        recording. "sophia" is in both catalogues today. Sarvam is the managed
        default, so its recording wins — the alternative is a picker playing one
        vendor's voice under the other's label, which is worse than a missing
        play button."""
        from scripts import generate_voice_samples as gen

        sarvam = {voice_id.lower() for voice_id, _ in gen._sarvam_voices_to_sample()}
        rumik = {v.lower() for v in gen.RUMIK_VOICES}
        shared = sarvam & rumik
        assert shared, "this guard is pointless if the catalogues stop overlapping"

        recorded: list[str] = []

        async def _fake_synth(client, *, api_key, voice, language):
            recorded.append(voice.lower())
            return b"RIFF"

        class _Storage:
            async def aget_file_metadata(self, path):
                return None

            async def acreate_file_from_bytes(self, path, data):
                return None

        monkeypatch.setattr(gen, "_vendor_key", AsyncMock(return_value="rk-test"))
        monkeypatch.setattr(gen, "_synthesise_rumik", _fake_synth)
        monkeypatch.setattr(gen, "get_storage", lambda: _Storage())

        await gen._rumik_samples(force=False)

        assert not (set(recorded) & shared)
        # Everything that does not clash is still recorded.
        assert set(recorded) == rumik - shared

    @pytest.mark.asyncio
    async def test_no_key_skips_rumik_without_failing_the_run(self, monkeypatch):
        """Unlike Sarvam, Rumik is not the managed default — a deployment with
        no Rumik key is an ordinary deployment, not a broken one."""
        from scripts import generate_voice_samples as gen

        monkeypatch.setattr(gen, "_vendor_key", AsyncMock(return_value=None))
        assert await gen._rumik_samples(force=False) == (0, 0, 0)
