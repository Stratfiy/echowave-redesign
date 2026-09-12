"""Every speaking template offers voices to hear: both genders, several
languages.

Scoped to the ones that speak. A template that never makes a sound -- a
scheduled bot, an internal knowledge bot -- would otherwise be required to
carry a gallery of six voices with play buttons, which is a choice offered,
stored, and then ignored on every run.
"""

from api.services.agent_templates import get_template, list_templates
from api.services.configuration import voice_samples


def test_every_template_suggests_a_man_and_a_woman_in_more_than_one_language():
    speaking = [t for t in list_templates() if t.speaks]
    assert speaking, "the catalogue lost every voice template"
    for template in speaking:
        voices = template.suggested_voices
        assert voices, template.id
        assert {v.gender for v in voices} >= {"male", "female"}, template.id
        assert len({v.language for v in voices}) >= 2, template.id
        assert all(v.language in voice_samples.SAMPLE_LANGUAGES for v in voices)


def test_one_template_carries_the_same_voices_when_fetched_alone():
    listed = next(t for t in list_templates() if t.id == "clinic_appointment")
    single = get_template("clinic_appointment")
    assert [v.voice_id for v in single.suggested_voices] == [
        v.voice_id for v in listed.suggested_voices
    ]


def test_a_silent_template_is_offered_no_voices():
    """The other half of the rule above, asserted rather than left implied.

    The fallback gave any template without its own list six default voices,
    so the first non-speaking one inherited a gallery. Nothing would have
    failed -- the voice would simply never be used.
    """
    silent = [t for t in list_templates() if not t.speaks]
    assert silent, "the catalogue has nothing that does not speak"
    for template in silent:
        assert template.suggested_voices == [], template.id


def test_every_sample_language_has_a_line_to_record():
    for language in voice_samples.SAMPLE_LANGUAGES:
        assert voice_samples.SAMPLE_LINES[language].strip()


def test_sample_paths_carry_voice_model_and_language():
    assert (
        voice_samples.sample_path("Anushka", "hi", "wav", "bulbul:v3")
        == "voice-samples/anushka-bulbul-v3-hi.wav"
    )
    assert (
        voice_samples.sample_path(
            "21m00Tcm4TlvDq8ikWAM", "ta", "mp3", "eleven_multilingual_v2"
        )
        == "voice-samples/21m00tcm4tlvdq8ikwam-eleven-multilingual-v2-ta.mp3"
    )
