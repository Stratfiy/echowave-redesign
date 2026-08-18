"""Reading a verification code down the phone.

The channel exists because SMS to an Indian number needs DLT registration and
transactional voice does not. What is tested here is mostly the things that
would make the call useless or unsafe rather than merely wrong:

* the digits are said one at a time, because "nine hundred and fourteen
  thousand" is the same number and no use to somebody writing it down
* the token is spent on first read, because it travels in a URL that the
  carrier and everything between here and it will log
* an unknown language falls back rather than raising, because refusing to
  verify somebody over an accent is worse than an accent
* an expired token still returns XML, because a carrier that gets an error page
  hangs up on a person who just answered a call from us
"""

from __future__ import annotations

import pytest

from api.services.telephony import voice_otp


class TestSayingTheDigits:
    def test_each_digit_is_separated(self):
        assert voice_otp._digits("914207") == "9 1 4 2 0 7"

    def test_the_code_is_read_out_twice(self):
        """Unconditional, because answering "say it again" would mean listening,
        and this call is deliberately a static announcement."""
        xml = voice_otp.speak_xml(voice_otp.SpokenCode(code="914207", language="en-IN"))
        assert xml.count("9 1 4 2 0 7") == 2

    def test_the_prompt_comes_before_the_digits(self):
        xml = voice_otp.speak_xml(voice_otp.SpokenCode(code="914207", language="en-IN"))
        assert xml.index("verification code is") < xml.index("9 1 4 2 0 7")

    def test_the_language_is_on_every_spoken_element(self):
        """Plivo picks a voice per <Speak>. One unmarked element and the digits
        come out in the default accent while the prompt does not."""
        xml = voice_otp.speak_xml(voice_otp.SpokenCode(code="914207", language="hi-IN"))
        assert xml.count("<Speak") == xml.count('language="hi-IN"')

    def test_hindi_speaks_hindi(self):
        xml = voice_otp.speak_xml(voice_otp.SpokenCode(code="914207", language="hi-IN"))
        assert "सत्यापन" in xml


class TestChoosingALanguage:
    def test_nothing_asked_for_is_english_india(self):
        assert voice_otp.resolve_language(None) == "en-IN"

    def test_a_bare_language_gets_the_indian_variant(self):
        assert voice_otp.resolve_language("hi") == "hi-IN"
        assert voice_otp.resolve_language("en") == "en-IN"

    def test_a_language_the_carrier_cannot_speak_falls_back(self):
        """Telugu and Tamil are real customer languages that Plivo's <Speak>
        does not have a voice for. Falling back is the honest failure: the
        digits still arrive. Synthesising with Sarvam and <Play> is the upgrade,
        and this test should change when that lands."""
        assert voice_otp.resolve_language("te") == "en-IN"
        assert voice_otp.resolve_language("ta-IN") == "en-IN"

    def test_an_unsupported_regional_variant_falls_back_to_its_base(self):
        assert voice_otp.resolve_language("en-AU") == "en-IN"

    def test_every_offered_language_has_a_prompt_and_a_repeat(self):
        """A language in the map with no prompt would read the digits with no
        introduction, which sounds like a wrong number."""
        for spoken in set(voice_otp.PLIVO_LANGUAGES.values()):
            assert spoken in voice_otp.PROMPTS
            assert spoken in voice_otp.REPEAT


class TestTheExpiredCall:
    def test_it_is_still_xml(self):
        assert voice_otp.unavailable_xml().startswith("<?xml")

    def test_it_says_something_rather_than_hanging_up(self):
        assert "<Speak" in voice_otp.unavailable_xml()

    def test_it_does_not_leak_a_code(self):
        assert not any(
            ch.isdigit() for ch in voice_otp.unavailable_xml().split("?>")[1]
        )


@pytest.mark.asyncio
class TestTheToken:
    async def test_a_stashed_code_comes_back(self):
        token = await voice_otp.stash("914207", language="hi")
        spoken = await voice_otp.claim(token)
        assert spoken is not None
        assert spoken.code == "914207"
        assert spoken.language == "hi-IN"

    async def test_it_can_only_be_claimed_once(self):
        """The whole reason the code is not in the URL. The answer URL reaches
        the carrier; a replayable token would let anyone who saw one hear the
        code."""
        token = await voice_otp.stash("914207", language=None)
        assert await voice_otp.claim(token) is not None
        assert await voice_otp.claim(token) is None

    async def test_an_unknown_token_is_not_an_error(self):
        assert await voice_otp.claim("not-a-real-token") is None

    async def test_tokens_are_not_guessable(self):
        tokens = {await voice_otp.stash("111111", language=None) for _ in range(20)}
        assert len(tokens) == 20
        assert all(len(t) >= 24 for t in tokens)

    async def test_the_code_is_not_in_the_token(self):
        """It travels in a URL. If the code were derivable from the token, every
        access log on the way would hold it."""
        token = await voice_otp.stash("914207", language=None)
        assert "914207" not in token
