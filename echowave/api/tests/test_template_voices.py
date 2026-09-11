"""Every template offers voices to hear: both genders, several languages."""

from api.services.agent_templates import get_template, list_templates
from api.services.configuration import voice_samples


def test_every_template_suggests_a_man_and_a_woman_in_more_than_one_language():
    for template in list_templates():
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
