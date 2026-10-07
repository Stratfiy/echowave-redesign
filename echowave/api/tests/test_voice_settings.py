"""Voice and language settings (screen 19, with stream settings).

Done when: the catalogue lists the languages Sarvam speaks with their voices
and invents nothing (no guessed genders); a chosen voice and its language
save together with speed and captions under one revision and read back the
same voice id; a voice that does not speak the language is refused rather
than swapped; a stale save is a conflict showing what is stored; preview is
honest when no sample can be recorded; and another person's settings never
move.
"""

from __future__ import annotations

import pytest

from api.services import member_preferences
from api.services.configuration import voice_samples
from api.services.voice import catalogue
from api.tests.support.voice import all_on, clean, client_as, make_people


@pytest.fixture
async def people(test_engine):
    p = await make_people("voice-v")
    yield p
    await clean(p)


class TestCatalogue:
    def test_every_offered_language_is_spoken_by_sarvam(self):
        for code in member_preferences.LANGUAGES:
            assert catalogue.voices_for(code), code
        assert catalogue.spoken_language("en") == "en-IN"

    def test_a_language_sarvam_cannot_speak_has_no_voices(self):
        assert catalogue.voices_for("ur-IN") == []
        assert catalogue.spoken_language("fr-FR") is None

    def test_ids_parse_and_unknown_ones_do_not(self):
        voice = catalogue.parse("sarvam:bulbul:v3:kavya")
        assert (
            voice is not None
            and voice.model == "bulbul:v3"
            and voice.speaker == "kavya"
        )
        assert catalogue.parse("sarvam:bulbul:v3:nobody") is None
        assert catalogue.parse("meera") is None

    def test_no_invented_attributes(self):
        listed = catalogue.as_dict()["languages"][0]["voices"][0]
        assert set(listed) == {"id", "label", "model"}


@pytest.mark.asyncio
class TestSaving:
    async def test_off_is_404(self, people):
        async with client_as(people.as_a) as c:
            assert (await c.get("/api/v1/voice/preferences")).status_code == 404
            assert (await c.get("/api/v1/voice/catalogue")).status_code == 404

    async def test_a_voice_saves_and_reads_back_the_same_id(self, people, monkeypatch):
        all_on(monkeypatch)
        async with client_as(people.as_a) as c:
            saved = await c.put(
                "/api/v1/voice/preferences",
                json={
                    "revision": 0,
                    "language": "ta-IN",
                    "voice": "sarvam:bulbul:v3:kavya",
                    "voice_speed": 1.25,
                    "captions": False,
                },
            )
            read = await c.get("/api/v1/voice/preferences")
        assert saved.status_code == 200
        assert read.json()["voice"] == "sarvam:bulbul:v3:kavya"
        assert read.json()["voice_speed"] == 1.25 and read.json()["captions"] is False
        assert read.json()["revision"] == 1

    async def test_a_voice_that_cannot_speak_the_language_is_refused(
        self, people, monkeypatch
    ):
        all_on(monkeypatch)
        async with client_as(people.as_a) as c:
            refused = await c.put(
                "/api/v1/voice/preferences",
                json={
                    "revision": 0,
                    "language": "ta-IN",
                    "voice": "sarvam:bulbul:v3:nobody",
                },
            )
        assert refused.status_code == 422
        assert (await member_preferences.get(people.a.id))["revision"] == 0

    async def test_stale_save_shows_what_is_stored(self, people, monkeypatch):
        all_on(monkeypatch)
        async with client_as(people.as_a) as c:
            await c.put(
                "/api/v1/voice/preferences", json={"revision": 0, "captions": True}
            )
            stale = await c.put(
                "/api/v1/voice/preferences", json={"revision": 0, "captions": False}
            )
        assert stale.status_code == 409
        assert stale.json()["detail"]["stored"]["captions"] is True

    @pytest.mark.parametrize("speed", [0.1, 3, "fast"])
    async def test_speed_outside_the_range_is_refused(self, people, monkeypatch, speed):
        all_on(monkeypatch)
        async with client_as(people.as_a) as c:
            refused = await c.put(
                "/api/v1/voice/preferences", json={"revision": 0, "voice_speed": speed}
            )
        assert refused.status_code == 422

    async def test_my_save_never_moves_a_colleagues(self, people, monkeypatch):
        all_on(monkeypatch)
        await member_preferences.save(people.b.id, {"language": "hi-IN"}, revision=0)
        async with client_as(people.as_a) as c:
            await c.put(
                "/api/v1/voice/preferences",
                json={"revision": 0, "language": "ta-IN", "voice_speed": 0.8},
            )
        theirs = await member_preferences.get(people.b.id)
        assert theirs["language"] == "hi-IN" and theirs["voice_speed"] is None


@pytest.mark.asyncio
class TestPreview:
    async def test_no_sample_and_no_key_is_needs_setup(self, people, monkeypatch):
        all_on(monkeypatch)

        async def none(**_):
            return None

        monkeypatch.setattr(voice_samples, "ensure_sample_url", none)
        async with client_as(people.as_a) as c:
            preview = await c.get(
                "/api/v1/voice/preview",
                params={"voice": "sarvam:bulbul:v3:kavya", "language": "ta-IN"},
            )
        assert preview.json() == {
            "state": "needs_setup",
            "url": None,
            "reason": "A preview could not be recorded: the voice service is not set up.",
        }

    async def test_a_recorded_sample_plays(self, people, monkeypatch):
        all_on(monkeypatch)
        asked = {}

        async def recorded(**kw):
            asked.update(kw)
            return "https://samples.example/kavya-ta.wav"

        monkeypatch.setattr(voice_samples, "ensure_sample_url", recorded)
        async with client_as(people.as_a) as c:
            preview = await c.get(
                "/api/v1/voice/preview",
                params={"voice": "sarvam:bulbul:v3:kavya", "language": "ta-IN"},
            )
        assert preview.json()["state"] == "available"
        assert asked == {
            "provider": "sarvam",
            "model": "bulbul:v3",
            "voice_id": "kavya",
            "language": "ta",
        }

    async def test_a_language_without_a_sample_line_says_so(self, people, monkeypatch):
        all_on(monkeypatch)
        async with client_as(people.as_a) as c:
            preview = await c.get(
                "/api/v1/voice/preview",
                params={"voice": "sarvam:bulbul:v3:kavya", "language": "bn-IN"},
            )
        assert preview.json()["state"] == "unavailable"
        assert "still speaks it" in preview.json()["reason"]
