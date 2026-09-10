"""The voice-sample generator must cover every Sarvam voice the picker shows.

v2 and v3 are different speaker sets (anushka/karun on v2, shubh/aditya on
v3) and an agent on either tier reaches the picker. Sampling one tier left
half the voices with no play button — the bug this guards against. The sample
path is keyed by voice id alone, so a name shared across tiers is recorded
once, under the first tier that lists it.
"""

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
