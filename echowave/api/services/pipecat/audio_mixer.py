"""Shared helper for building audio output mixers used by telephony transports."""

import os
from pathlib import Path

from loguru import logger

from api.constants import APP_ROOT_DIR
from api.services.pipecat.audio_file_cache import get_cached_ambient_noise_path
from pipecat.audio.mixers.silence_mixer import SilenceAudioMixer
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer

librnnoise_path = os.path.normpath(
    str(APP_ROOT_DIR / "native" / "rnnoise" / "librnnoise.so")
)


def _is_playable(path: Path | str) -> bool:
    """Whether SoundfileMixer will be able to open this file.

    Checked before the mixer is built rather than after, because
    ``SoundfileMixer`` swallows the open failure at construction and then
    fails on *every* frame it is asked to mix. The output transport counts
    those failures and cancels the pipeline at ten — which is roughly five
    seconds of audio, so a missing decorative background sound ends the call
    just after the greeting, with nothing in the UI but a hung
    "Establishing Connection".

    That is what a fresh deployment does today: ``.gitignore`` excludes
    ``*.wav``, so ``api/assets/office-ambience-*.wav`` has never been in a
    clone or an image, while the default config asks for it by name.
    """
    try:
        import soundfile

        soundfile.info(str(path))
        return True
    except Exception as e:  # unreadable, absent, or not audio
        logger.warning(
            f"Ambient noise file {path} cannot be played ({e}); "
            "continuing with silence. Background audio is cosmetic and must "
            "never be the reason a call drops."
        )
        return False


async def build_audio_out_mixer(
    audio_out_sample_rate: int,
    ambient_noise_config: dict | None,
):
    """Build the audio output mixer based on the ambient noise configuration.

    Returns a ``SoundfileMixer`` when ambient noise is enabled, or a
    ``SilenceAudioMixer`` otherwise.  Supports custom user-uploaded audio
    files via the ``storage_key`` / ``storage_backend`` fields in the config.
    """
    if not ambient_noise_config or not ambient_noise_config.get("enabled", False):
        return SilenceAudioMixer()

    volume = ambient_noise_config.get("volume", 0.3)

    storage_key = ambient_noise_config.get("storage_key")
    storage_backend = ambient_noise_config.get("storage_backend")

    if storage_key and storage_backend:
        cached_path = await get_cached_ambient_noise_path(
            storage_key, storage_backend, audio_out_sample_rate
        )
        if cached_path and _is_playable(cached_path):
            return SoundfileMixer(
                sound_files={"custom": cached_path},
                default_sound="custom",
                volume=volume,
            )
        logger.warning("Custom ambient noise file unavailable, falling back to default")

    default_path = (
        APP_ROOT_DIR / "assets" / f"office-ambience-{audio_out_sample_rate}-mono.wav"
    )
    if not _is_playable(default_path):
        return SilenceAudioMixer()

    return SoundfileMixer(
        sound_files={"office": default_path},
        default_sound="office",
        volume=volume,
    )
