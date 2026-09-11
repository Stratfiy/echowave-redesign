"""A voice preview exists because somebody pressed play, not because an
operator remembered to run a script.

The samples were "pre-generated" by ``scripts/generate_voice_samples.py``, and
nothing at runtime depended on it having been run. It never was run against
production, so every picker showed names and no play buttons for months while
the missing previews were reported over and over. The design had a human in
the middle of a cache, and the human is the part that failed.

So the cache fills itself. The list stays a pure lookup -- it asks about forty
voices at once -- and the recording happens on the press.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.services.configuration import voice_samples, voice_synthesis


@pytest.mark.asyncio
class TestTheListStillOnlyLooksUp:
    """Forty vendor calls to draw a list is not a page anybody waits for."""

    async def test_a_missing_sample_is_none_and_records_nothing(self):
        with (
            patch.object(voice_samples, "get_storage") as storage,
            patch.object(voice_synthesis, "synthesise", new=AsyncMock()) as synth,
        ):
            storage.return_value.aget_file_metadata = AsyncMock(return_value=None)
            assert await voice_samples.sample_url("ritu", "en") is None
        synth.assert_not_awaited()


@pytest.mark.asyncio
class TestPressingPlayRecordsItOnce:
    async def test_an_unrecorded_voice_is_synthesised_and_stored(self):
        with (
            patch.object(voice_samples, "get_storage") as storage,
            patch.object(voice_samples, "_vendor_key", new=AsyncMock(return_value="k")),
            patch.object(
                voice_synthesis, "synthesise", new=AsyncMock(return_value=b"RIFFwav")
            ) as synth,
        ):
            fs = storage.return_value
            fs.aget_file_metadata = AsyncMock(return_value=None)
            fs.acreate_file_from_bytes = AsyncMock()
            fs.aget_signed_url = AsyncMock(return_value="https://s/ritu-en.wav")

            url = await voice_samples.ensure_sample_url(
                provider="sarvam", model="bulbul:v3", voice_id="ritu", language="en"
            )

        assert url == "https://s/ritu-en.wav"
        synth.assert_awaited_once()
        fs.acreate_file_from_bytes.assert_awaited_once()
        # The model is in the key, so Ritu on Bulbul v3 and Ritu on Bulbul v2
        # are two recordings rather than whichever one was asked for first.
        assert fs.acreate_file_from_bytes.await_args.args[0].endswith(
            "ritu-bulbul-v3-en.wav"
        )

    async def test_an_already_recorded_voice_calls_no_vendor(self):
        """The whole point of storing it: the second listener, and every
        listener in every other account, pays nothing."""
        with (
            patch.object(voice_samples, "get_storage") as storage,
            patch.object(voice_synthesis, "synthesise", new=AsyncMock()) as synth,
        ):
            fs = storage.return_value
            fs.aget_file_metadata = AsyncMock(return_value={"size": 1})
            fs.aget_signed_url = AsyncMock(return_value="https://s/ritu-en.wav")

            url = await voice_samples.ensure_sample_url(
                provider="sarvam", model="bulbul:v3", voice_id="ritu", language="en"
            )

        assert url == "https://s/ritu-en.wav"
        synth.assert_not_awaited()

    async def test_an_elevenlabs_sample_is_stored_as_mp3(self):
        with (
            patch.object(voice_samples, "get_storage") as storage,
            patch.object(voice_samples, "_vendor_key", new=AsyncMock(return_value="k")),
            patch.object(
                voice_synthesis, "synthesise", new=AsyncMock(return_value=b"ID3")
            ),
        ):
            fs = storage.return_value
            fs.aget_file_metadata = AsyncMock(return_value=None)
            fs.acreate_file_from_bytes = AsyncMock()
            fs.aget_signed_url = AsyncMock(return_value="https://s/x.mp3")

            await voice_samples.ensure_sample_url(
                provider="elevenlabs",
                model="eleven_flash_v2_5",
                voice_id="21m00Tcm4TlvDq8ikWAM",
                language="en",
            )

        assert fs.acreate_file_from_bytes.await_args.args[0].endswith(".mp3")


@pytest.mark.asyncio
class TestItSaysNoRatherThanFailing:
    """The caller is a play button. Every one of these is an honest "you
    cannot hear this", and none of them is an exception reaching a screen."""

    async def _url(self, **kw):
        with (
            patch.object(voice_samples, "get_storage") as storage,
            patch.object(voice_samples, "_vendor_key", new=AsyncMock(return_value="k")),
        ):
            fs = storage.return_value
            fs.aget_file_metadata = AsyncMock(return_value=None)
            fs.acreate_file_from_bytes = AsyncMock()
            fs.aget_signed_url = AsyncMock(return_value="https://s/x")
            return await voice_samples.ensure_sample_url(**kw)

    async def test_a_language_with_no_sample_line(self):
        url = await self._url(
            provider="sarvam", model="bulbul:v3", voice_id="ritu", language="fr"
        )
        assert url is None

    async def test_no_platform_key_for_that_vendor(self):
        with (
            patch.object(voice_samples, "get_storage") as storage,
            patch.object(
                voice_samples, "_vendor_key", new=AsyncMock(return_value=None)
            ),
            patch.object(voice_synthesis, "synthesise", new=AsyncMock()) as synth,
        ):
            storage.return_value.aget_file_metadata = AsyncMock(return_value=None)
            url = await voice_samples.ensure_sample_url(
                provider="rumik", model="mulberry", voice_id="emma", language="en"
            )
        assert url is None
        synth.assert_not_awaited()

    async def test_a_vendor_that_refuses(self):
        with (
            patch.object(voice_samples, "get_storage") as storage,
            patch.object(voice_samples, "_vendor_key", new=AsyncMock(return_value="k")),
            patch.object(
                voice_synthesis,
                "synthesise",
                new=AsyncMock(side_effect=RuntimeError("502")),
            ),
        ):
            storage.return_value.aget_file_metadata = AsyncMock(return_value=None)
            url = await voice_samples.ensure_sample_url(
                provider="sarvam", model="bulbul:v3", voice_id="ritu", language="en"
            )
        assert url is None

    async def test_storage_that_cannot_keep_it(self):
        with (
            patch.object(voice_samples, "get_storage") as storage,
            patch.object(voice_samples, "_vendor_key", new=AsyncMock(return_value="k")),
            patch.object(
                voice_synthesis, "synthesise", new=AsyncMock(return_value=b"x")
            ),
        ):
            fs = storage.return_value
            fs.aget_file_metadata = AsyncMock(return_value=None)
            fs.acreate_file_from_bytes = AsyncMock(side_effect=OSError("disk"))
            url = await voice_samples.ensure_sample_url(
                provider="sarvam", model="bulbul:v3", voice_id="ritu", language="en"
            )
        assert url is None


class TestWhatThereIsNoHonestSampleFor:
    """Better no button than a confident recording of a model mispronouncing
    a language it does not know."""

    @pytest.mark.asyncio
    async def test_rumik_muga_has_no_speaker_to_record(self):
        with pytest.raises(voice_synthesis.UnsupportedVoice):
            await voice_synthesis.synthesise(
                provider="rumik",
                model="muga",
                voice="emma",
                language="en",
                api_key="k",
            )

    @pytest.mark.asyncio
    async def test_rumik_does_not_speak_tamil(self):
        with pytest.raises(voice_synthesis.UnsupportedVoice):
            await voice_synthesis.synthesise(
                provider="rumik",
                model="mulberry",
                voice="emma",
                language="ta",
                api_key="k",
            )

    @pytest.mark.asyncio
    async def test_a_vendor_with_no_recorder_written(self):
        with pytest.raises(voice_synthesis.UnsupportedVoice):
            await voice_synthesis.synthesise(
                provider="cartesia",
                model="sonic-3",
                voice="x",
                language="en",
                api_key="k",
            )

    def test_rumik_languages_are_only_the_ones_it_speaks(self):
        assert set(voice_synthesis.rumik_languages()) <= {"hi", "en"}


class TestSmallestCanBeHeardAtLast:
    """Smallest ships 21 voices and, until now, no way to hear any of them:
    the recorder handled Sarvam, ElevenLabs and Rumik and fell through to
    "no sample" for everything else. A picker that cannot play a voice is a
    list of names, which is the problem samples exist to solve."""

    async def test_it_posts_the_contract_the_call_path_uses(self):
        """Same field names pipecat's own Smallest client puts on the wire, so
        the sample is recorded through what will actually speak."""
        captured = {}

        class _Response:
            content = b"RIFFwav"

            def raise_for_status(self):
                return None

        class _Client:
            async def post(self, url, **kwargs):
                captured["url"] = url
                captured.update(kwargs)
                return _Response()

        audio = await voice_synthesis.synthesise_smallest(
            _Client(), api_key="k", voice="niharika", language="ta"
        )

        assert audio == b"RIFFwav"
        assert captured["url"] == voice_synthesis.SMALLEST_TTS_URL
        assert captured["headers"]["Authorization"] == "Bearer k"
        body = captured["json"]
        assert body["voice_id"] == "niharika"
        assert body["language"] == "ta"
        assert body["output_format"] == "wav"
        assert body["text"] == voice_samples.SAMPLE_LINES["ta"]

    async def test_the_model_is_carried_through_not_fixed(self):
        """The whole point of the key change: Lightning and Lightning Pro are
        two prices and must be two recordings."""
        seen = []

        class _Response:
            content = b"RIFFwav"

            def raise_for_status(self):
                return None

        class _Client:
            async def post(self, url, **kwargs):
                seen.append(kwargs["json"]["model"])
                return _Response()

        await voice_synthesis.synthesise_smallest(
            _Client(),
            api_key="k",
            voice="meher",
            language="hi",
            model="lightning_v3.1_pro",
        )
        await voice_synthesis.synthesise_smallest(
            _Client(), api_key="k", voice="meher", language="hi"
        )

        assert seen == ["lightning_v3.1_pro", voice_synthesis.SMALLEST_SAMPLE_MODEL]

    async def test_a_language_it_does_not_speak_is_declined_not_guessed(self):
        """Telugu, specifically. The vendor's current page says it speaks ten
        Indic languages including Telugu; this repository's own language list
        and pipecat's map both say it does not. One page read once does not
        outvote two sources in the tree, and the cost of being wrong is a
        stored recording of a confident mispronunciation."""
        with pytest.raises(voice_synthesis.UnsupportedVoice):
            await voice_synthesis.synthesise(
                provider="smallest",
                model="lightning_v3.1",
                voice="arjun",
                language="te",
                api_key="k",
            )

    def test_the_language_set_is_derived_not_retyped(self):
        """If the vendor's support changes, it changes in one place and every
        caller follows. A hardcoded copy here is how the two drift apart."""
        from api.services.configuration.options.smallest import (
            SMALLEST_TTS_LANGUAGES,
        )

        assert voice_synthesis.SMALLEST_LANGUAGES <= set(SMALLEST_TTS_LANGUAGES)
        assert voice_synthesis.SMALLEST_LANGUAGES <= set(voice_samples.SAMPLE_LANGUAGES)
        assert "ta" in voice_synthesis.SMALLEST_LANGUAGES
