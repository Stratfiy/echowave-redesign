"""Live voice reads the person's own voice settings (screen 19, saved by
stream `settings` in Settings -> Voice and language).

Done when: a session starts with the person's `speaking_speed` and
`captions` from member_preferences; their voice applies only if the
workspace's voice model has that speaker and speaks the session's language,
and is otherwise left out rather than swapped; and a colleague's settings
never reach another person's session.
"""

from __future__ import annotations

import pytest

from api.services import member_preferences
from api.services.voice import catalogue, readiness
from api.tests.support.voice import all_on, clean, client_as, make_people


@pytest.fixture
async def people(test_engine):
    p = await make_people("voice-v")
    yield p
    await clean(p)


@pytest.fixture
def ready(monkeypatch):
    async def available(**_):
        return readiness.Readiness(
            readiness.AVAILABLE,
            config={
                "stt": {"provider": "sarvam", "model": "saaras:v3"},
                "tts": {"provider": "sarvam", "model": "bulbul:v3"},
                "language": "ta-IN",
            },
        )

    monkeypatch.setattr(readiness, "live_voice", available)


class TestCatalogue:
    def test_offered_languages_are_spoken_or_said_to_be_text_only(self):
        spoken = {c for c in member_preferences.LANGUAGES if catalogue.spoken_language(c)}
        # The Indian languages Sarvam's voice speaks, and English.
        assert {"en", "en-IN", "hi-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN"} <= spoken
        assert catalogue.spoken_language("en") == "en-IN"
        # Urdu is a text language here: typed and captioned, not spoken.
        assert catalogue.spoken_language("ur-IN") is None

    def test_a_voice_is_a_speaker_of_one_model(self):
        assert catalogue.is_speaker("kavya", "bulbul:v3")
        assert not catalogue.is_speaker("kavya", "bulbul:v2")
        assert catalogue.is_speaker("anushka", "bulbul:v2")
        assert not catalogue.is_speaker("nobody", "bulbul:v3")
        assert not catalogue.is_speaker(None, "bulbul:v3")

    def test_compatible_needs_both_language_and_speaker(self):
        assert catalogue.compatible("kavya", "ta-IN", "bulbul:v3")
        assert not catalogue.compatible("kavya", "ur-IN", "bulbul:v3")
        assert not catalogue.compatible("anushka", "ta-IN", "bulbul:v3")


@pytest.mark.asyncio
class TestTheSessionReadsSettings:
    async def test_speed_captions_and_voice_from_settings(
        self, people, monkeypatch, ready
    ):
        all_on(monkeypatch)
        await member_preferences.save(
            people.a.id,
            {
                "language": "ta-IN",
                "voice": "kavya",
                "speaking_speed": 1.25,
                "captions": False,
            },
            revision=0,
        )
        async with client_as(people.as_a) as c:
            started = await c.post("/api/v1/voice/sessions", json={})
            state = await c.get("/api/v1/voice/readiness")
        assert started.status_code == 201
        body = started.json()
        assert body["voice"] == "kavya"
        assert body["config"]["speed"] == 1.25
        assert body["config"]["captions"] is False
        assert body["config"]["voice_compatible"] is True
        assert state.json()["captions"] is False

    async def test_a_voice_the_model_lacks_is_left_out_not_swapped(
        self, people, monkeypatch, ready
    ):
        all_on(monkeypatch)
        await member_preferences.save(people.a.id, {"voice": "anushka"}, revision=0)
        async with client_as(people.as_a) as c:
            started = await c.post("/api/v1/voice/sessions", json={})
        body = started.json()
        assert body["voice"] is None
        assert body["config"]["voice_compatible"] is False

    async def test_nothing_set_reads_as_normal_speed_and_captions_on(
        self, people, monkeypatch, ready
    ):
        all_on(monkeypatch)
        async with client_as(people.as_a) as c:
            body = (await c.post("/api/v1/voice/sessions", json={})).json()
        assert body["config"]["speed"] is None
        assert body["config"]["captions"] is True

    async def test_a_colleagues_settings_never_reach_my_session(
        self, people, monkeypatch, ready
    ):
        all_on(monkeypatch)
        await member_preferences.save(
            people.b.id, {"voice": "kavya", "speaking_speed": 0.6}, revision=0
        )
        async with client_as(people.as_a) as c:
            body = (await c.post("/api/v1/voice/sessions", json={})).json()
        assert body["voice"] is None and body["config"]["speed"] is None

    async def test_the_old_voice_settings_routes_are_gone(self, people, monkeypatch):
        all_on(monkeypatch)
        async with client_as(people.as_a) as c:
            for path in ("/api/v1/voice/catalogue", "/api/v1/voice/preferences"):
                assert (await c.get(path)).status_code == 404
