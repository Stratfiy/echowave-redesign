"""Asking a vendor for the voices only it can enumerate.

The live call cannot be exercised here -- it needs a key and the vendor's
host -- so what is pinned is everything around it: the shape we read out of
their response, the caching that keeps a config screen off the network, and
the rule that a failure degrades to the local catalogue's message rather than
to an empty picker.
"""

from __future__ import annotations

import httpx
import pytest

from api.services.configuration import vendor_voices


@pytest.fixture(autouse=True)
def _clear():
    vendor_voices.clear_cache()
    yield
    vendor_voices.clear_cache()


class TestReadingElevenLabsVoices:
    def test_it_takes_the_vendors_own_preview(self):
        """Their hosted sample is passed through rather than re-recorded: the
        sample pipeline exists for providers that publish none."""
        voices = vendor_voices._elevenlabs_voices(
            {
                "voices": [
                    {
                        "voice_id": "abc",
                        "name": "Rachel",
                        "preview_url": "https://cdn.example/rachel.mp3",
                        "labels": {"gender": "female", "accent": "american"},
                    }
                ]
            }
        )

        assert voices[0].preview_url == "https://cdn.example/rachel.mp3"
        assert voices[0].gender == "female"
        assert voices[0].accent == "american"

    def test_labels_are_optional_because_their_author_fills_them_in(self):
        """``labels`` is free-form on their side, so a voice carrying none is
        ordinary rather than malformed."""
        voices = vendor_voices._elevenlabs_voices(
            {"voices": [{"voice_id": "abc", "name": "Bare"}]}
        )

        assert voices[0].gender is None
        assert voices[0].preview_url is None

    def test_a_voice_with_no_id_is_dropped(self):
        """An id is the only field a call actually needs; a row without one
        would render a picker entry that cannot be chosen."""
        voices = vendor_voices._elevenlabs_voices(
            {"voices": [{"name": "No id"}, {"voice_id": "ok", "name": "Fine"}]}
        )

        assert [v.voice_id for v in voices] == ["ok"]

    def test_a_nameless_voice_shows_its_id(self):
        voices = vendor_voices._elevenlabs_voices({"voices": [{"voice_id": "xyz"}]})

        assert voices[0].name == "xyz"

    def test_an_empty_body_is_not_an_error(self):
        assert vendor_voices._elevenlabs_voices({}) == []


class TestWhichProvidersCanBeAsked:
    def test_elevenlabs_can(self):
        assert vendor_voices.can_fetch("elevenlabs")

    def test_a_provider_with_a_fixed_list_cannot(self):
        """Sarvam's voices ship with the code, so asking would be a network
        call for an answer we already have."""
        assert not vendor_voices.can_fetch("sarvam")

    @pytest.mark.asyncio
    async def test_fetching_one_we_cannot_ask_returns_none(self):
        assert await vendor_voices.fetch("sarvam") is None


class TestFailingSafely:
    @pytest.mark.asyncio
    async def test_no_platform_key_returns_none_rather_than_empty(self, monkeypatch):
        """None and [] mean different things to the caller: one shows the
        local catalogue's reason, the other would claim the account has no
        voices at all."""

        async def no_key(*_args, **_kwargs):
            return None

        monkeypatch.setattr(
            vendor_voices.platform_credentials, "resolve_api_key", no_key
        )

        assert await vendor_voices.fetch("elevenlabs") is None

    @pytest.mark.asyncio
    async def test_a_vendor_error_returns_none(self, monkeypatch):
        async def a_key(*_args, **_kwargs):
            return "platform-key"

        async def boom(_key):
            raise RuntimeError("elevenlabs is down")

        monkeypatch.setattr(
            vendor_voices.platform_credentials, "resolve_api_key", a_key
        )
        monkeypatch.setitem(
            vendor_voices._FETCHERS,
            "elevenlabs",
            (vendor_voices.CostComponent.TTS, boom),
        )

        assert await vendor_voices.fetch("elevenlabs") is None

    @pytest.mark.asyncio
    async def test_a_failure_is_not_cached(self, monkeypatch):
        """A vendor blip must not blank the picker for the whole TTL."""
        calls = []

        async def a_key(*_args, **_kwargs):
            return "platform-key"

        async def boom(_key):
            calls.append(1)
            raise RuntimeError("transient")

        monkeypatch.setattr(
            vendor_voices.platform_credentials, "resolve_api_key", a_key
        )
        monkeypatch.setitem(
            vendor_voices._FETCHERS,
            "elevenlabs",
            (vendor_voices.CostComponent.TTS, boom),
        )

        await vendor_voices.fetch("elevenlabs")
        await vendor_voices.fetch("elevenlabs")

        assert len(calls) == 2


class TestCaching:
    @pytest.mark.asyncio
    async def test_a_second_look_does_not_hit_the_vendor(self, monkeypatch):
        """Auditioning voices means clicking every one twice; that is one
        request, not fourteen."""
        calls = []

        async def a_key(*_args, **_kwargs):
            return "platform-key"

        async def once(_key):
            calls.append(1)
            return [vendor_voices.VendorVoice(voice_id="abc", name="Rachel")]

        monkeypatch.setattr(
            vendor_voices.platform_credentials, "resolve_api_key", a_key
        )
        monkeypatch.setitem(
            vendor_voices._FETCHERS,
            "elevenlabs",
            (vendor_voices.CostComponent.TTS, once),
        )

        first = await vendor_voices.fetch("elevenlabs")
        second = await vendor_voices.fetch("elevenlabs")

        assert len(calls) == 1
        assert first == second

    @pytest.mark.asyncio
    async def test_an_expired_entry_is_asked_again(self, monkeypatch):
        calls = []

        async def a_key(*_args, **_kwargs):
            return "platform-key"

        async def each_time(_key):
            calls.append(1)
            return [vendor_voices.VendorVoice(voice_id="abc", name="Rachel")]

        monkeypatch.setattr(
            vendor_voices.platform_credentials, "resolve_api_key", a_key
        )
        monkeypatch.setitem(
            vendor_voices._FETCHERS,
            "elevenlabs",
            (vendor_voices.CostComponent.TTS, each_time),
        )
        monkeypatch.setattr(vendor_voices, "_TTL_SECONDS", -1)

        await vendor_voices.fetch("elevenlabs")
        await vendor_voices.fetch("elevenlabs")

        assert len(calls) == 2


class TestSayingWhatTheVendorSaid:
    """A 401 is not one failure, and the log has to say which one it was.

    ``HTTPStatusError`` stringifies to the status and the URL, so an
    unrecognised key, a revoked key and a key merely missing ``voices_read``
    all produce the same line. The vendor distinguishes them in the body, and
    each has a different fix.
    """

    def _status_error(self, status: int, body: str) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "https://api.elevenlabs.io/v1/voices")
        response = httpx.Response(status, text=body, request=request)
        return httpx.HTTPStatusError("boom", request=request, response=response)

    def test_the_body_is_carried_into_the_message(self):
        described = vendor_voices._describe(
            self._status_error(401, '{"detail":{"status":"missing_permissions"}}')
        )

        assert "missing_permissions" in described

    def test_two_flavours_of_401_do_not_read_alike(self):
        missing = vendor_voices._describe(
            self._status_error(401, '{"detail":{"status":"missing_permissions"}}')
        )
        invalid = vendor_voices._describe(
            self._status_error(401, '{"detail":{"status":"invalid_api_key"}}')
        )

        assert missing != invalid

    def test_an_ordinary_exception_is_left_alone(self):
        assert vendor_voices._describe(RuntimeError("timed out")) == "timed out"

    def test_an_empty_body_falls_back_to_the_exception(self):
        described = vendor_voices._describe(self._status_error(503, "   "))

        assert described == "boom"

    def test_a_gateway_serving_html_does_not_fill_the_log(self):
        described = vendor_voices._describe(
            self._status_error(502, "<html>" + "x" * 5000)
        )

        assert len(described) < 600
        assert described.endswith("...")
