"""Ambient noise is decoration. It must never be why a call drops.

The office-ambience mixer took a production call down end to end, and the
chain is worth stating because nothing in it looks dangerous on its own:

1. ``.gitignore`` excludes ``*.wav``, so ``api/assets/office-ambience-*.wav``
   has never existed in a clone or a built image — only on the machine of
   whoever first added the feature.
2. The default ambient-noise config asks for that file by name.
3. ``SoundfileMixer`` swallows the open error at construction. It reports the
   failure once, to the log, and then returns a broken mixer.
4. Every subsequent frame fails to mix. The output transport counts
   consecutive failures and cancels the pipeline at ten.

Ten frames is about five seconds. So the call died just after connecting, and
the browser sat on "Establishing Connection..." forever with no error — the
observed production symptom, from a background sound nobody asked for.

These tests pin the rule that falls out of that: if the file cannot be played,
the call proceeds in silence.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from api.services.pipecat.audio_mixer import _is_playable, build_audio_out_mixer
from pipecat.audio.mixers.silence_mixer import SilenceAudioMixer
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer


@pytest.fixture
def playable_wav(tmp_path) -> Path:
    path = tmp_path / "ambience.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 16000)
    return path


class TestIsPlayable:
    def test_a_real_wav_is_playable(self, playable_wav):
        assert _is_playable(playable_wav) is True

    def test_a_missing_file_is_not(self, tmp_path):
        assert _is_playable(tmp_path / "nope.wav") is False

    def test_a_file_that_is_not_audio_is_not(self, tmp_path):
        """The failure mode is 'cannot be played', not 'does not exist'."""
        path = tmp_path / "not-audio.wav"
        path.write_text("this is not a wav file")
        assert _is_playable(path) is False


class TestTheMixerFallsBackToSilence:
    async def test_a_missing_default_asset_yields_silence(self, monkeypatch, tmp_path):
        """The production case: ambient noise on, asset absent from the image."""
        monkeypatch.setattr(
            "api.services.pipecat.audio_mixer.APP_ROOT_DIR", tmp_path
        )

        mixer = await build_audio_out_mixer(16000, {"enabled": True})

        assert isinstance(mixer, SilenceAudioMixer)

    async def test_a_present_default_asset_is_used(self, monkeypatch, tmp_path):
        assets = tmp_path / "assets"
        assets.mkdir()
        target = assets / "office-ambience-16000-mono.wav"
        with wave.open(str(target), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes(b"\x00\x00" * 16000)
        monkeypatch.setattr(
            "api.services.pipecat.audio_mixer.APP_ROOT_DIR", tmp_path
        )

        mixer = await build_audio_out_mixer(16000, {"enabled": True})

        assert isinstance(mixer, SoundfileMixer)

    async def test_disabled_ambient_noise_is_still_silence(self):
        assert isinstance(await build_audio_out_mixer(16000, None), SilenceAudioMixer)
        assert isinstance(
            await build_audio_out_mixer(16000, {"enabled": False}), SilenceAudioMixer
        )

    async def test_an_unplayable_custom_upload_does_not_kill_the_call(
        self, monkeypatch, tmp_path
    ):
        """A customer uploading a corrupt file gets silence, not a dropped call."""
        broken = tmp_path / "corrupt.wav"
        broken.write_text("nope")

        async def fake_cache(*args, **kwargs):
            return broken

        monkeypatch.setattr(
            "api.services.pipecat.audio_mixer.get_cached_ambient_noise_path",
            fake_cache,
        )
        monkeypatch.setattr(
            "api.services.pipecat.audio_mixer.APP_ROOT_DIR", tmp_path
        )

        mixer = await build_audio_out_mixer(
            16000,
            {"enabled": True, "storage_key": "k", "storage_backend": "s3"},
        )

        assert isinstance(mixer, SilenceAudioMixer)
