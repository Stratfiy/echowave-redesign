"""The agent panel names its voice, never prints the provider's id.

Production showed Voice: "HBlqQDCBvQxsEK8OFtEZ" -- an ElevenLabs library
voice. The model row only looked in the local catalogue (ElevenLabs' premade
voices), so a voice from the account's library came back unnamed and the
screen fell through to the raw id.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.services.configuration import agent_options, vendor_voices
from api.services.configuration.options.elevenlabs import ELEVENLABS_PREMADE_VOICES

LIBRARY_ID = "HBlqQDCBvQxsEK8OFtEZ"


def _library(name="Priya", language="hi"):
    return [
        vendor_voices.VendorVoice(
            voice_id=LIBRARY_ID, name=name, gender="female", language=language
        )
    ]


@pytest.mark.asyncio
class TestTheVoiceIsNamed:
    async def test_a_library_voice_is_named_from_the_vendor(self):
        with patch.object(
            vendor_voices, "fetch", new=AsyncMock(return_value=_library())
        ):
            options = await agent_options.voice_options_for(
                "elevenlabs", "eleven_flash_v2_5", LIBRARY_ID
            )
        named = [o for o in options if o["voice_id"] == LIBRARY_ID]
        assert named == [
            {
                "voice_id": LIBRARY_ID,
                "name": "Priya",
                "gender": "female",
                "description": "Hindi",
                "is_default": False,
            }
        ]
        # The local list is still there for the picker.
        assert len(options) == len(ELEVENLABS_PREMADE_VOICES) + 1

    async def test_a_premade_voice_does_not_ask_the_vendor(self):
        premade = ELEVENLABS_PREMADE_VOICES[0][0]
        with patch.object(vendor_voices, "fetch", new=AsyncMock()) as fetch:
            options = await agent_options.voice_options_for(
                "elevenlabs", "eleven_flash_v2_5", premade
            )
        fetch.assert_not_awaited()
        assert any(o["voice_id"] == premade for o in options)

    async def test_a_vendor_that_cannot_be_asked_leaves_it_unnamed_not_broken(self):
        with patch.object(vendor_voices, "fetch", new=AsyncMock(return_value=None)):
            options = await agent_options.voice_options_for(
                "elevenlabs", "eleven_flash_v2_5", LIBRARY_ID
            )
        assert all(o["voice_id"] != LIBRARY_ID for o in options)
        assert len(options) == len(ELEVENLABS_PREMADE_VOICES)

    async def test_an_id_echoed_back_as_its_name_is_not_a_name(self):
        with patch.object(
            vendor_voices,
            "fetch",
            new=AsyncMock(return_value=_library(name=LIBRARY_ID)),
        ):
            options = await agent_options.voice_options_for(
                "elevenlabs", "eleven_flash_v2_5", LIBRARY_ID
            )
        assert all(o["voice_id"] != LIBRARY_ID for o in options)
