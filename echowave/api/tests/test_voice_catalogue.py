"""The voice picker, which quietly gated the whole product.

Voices came from an external service that no longer exists, so every picker
read "Failed to load voices", TTS could not be configured, and an agent with no
voice cannot place a call. These tests are about the two things that matter now:
a managed customer seeing the voices they will really get, and "we cannot list
them" never looking like "there are none".
"""

from api.services.configuration import voice_catalogue as vc


class TestServingLocally:
    def test_sarvam_returns_its_published_voices(self):
        assert len(vc.for_provider("sarvam", model="bulbul:v2").voices) == 7

    def test_the_model_version_picks_the_right_set(self):
        """A v3 name sent to a v2 model is rejected by the vendor at call time,
        which is a confusing way to find a config error."""
        v2 = {v.voice_id for v in vc.for_provider("sarvam", model="bulbul:v2").voices}
        v3 = {v.voice_id for v in vc.for_provider("sarvam", model="bulbul:v3").voices}
        assert v2 != v3

    def test_managed_resolves_to_the_provider_actually_serving_it(self):
        """A managed customer must see the voices they will get, not a list
        belonging to a vendor they imagine is behind the tier."""
        managed = vc.for_provider("decibyl", model="default")
        assert managed.provider == "sarvam"
        assert managed.voices


class TestBeingHonestAboutLimits:
    def test_account_specific_providers_say_why_rather_than_returning_nothing(self):
        """Empty and unlistable must not look the same. One means pick another
        provider; the other means paste an ID."""
        cat = vc.for_provider("elevenlabs")
        assert cat.voices == []
        assert cat.unavailable_reason and "your account" in cat.unavailable_reason

    def test_an_unknown_provider_still_explains_itself(self):
        cat = vc.for_provider("some-provider-we-never-added")
        assert cat.unavailable_reason


class TestThePickerControls:
    def test_gender_filters(self):
        female = vc.filtered("sarvam", model="bulbul:v2", gender="female").voices
        assert female and all(v.gender == "female" for v in female)

    def test_search_matches_the_id_as_well_as_the_name(self):
        assert vc.filtered("sarvam", model="bulbul:v2", q="anush").voices

    def test_facets_are_computed_unfiltered(self):
        """A gender dropdown listing only the genders that survive the current
        filter cannot be used to change that filter."""
        assert set(vc.facets("sarvam", model="bulbul:v2")["genders"]) == {
            "female",
            "male",
        }
