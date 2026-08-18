"""Reading a verification code down the phone.

The channel `verification_sender` documents and never had. SMS to an Indian
number needs DLT registration — a Principal Entity, a registered header, a
registered template, roughly ₹5,900 and 7–10 business days — and unregistered
traffic is dropped by the operator while looking like success. Transactional
*voice* carries no such template requirement, and the thing a trial user is
verifying is precisely that Decibyl can ring them. So the platform verifies by
calling.

**The code is spoken, not entered.** Twilio does the opposite: it shows the
code on screen and captures DTMF. Both prove possession of the handset. Speaking
it means the person types it into our app, which is one fewer carrier feature to
depend on and no DTMF capture to build — and it keeps the call a static
announcement rather than a live pipeline, which matters because this is the flow
that gates every trial account. A verification call that depends on STT, VAD and
a model being up has more ways to fail than the thing it is verifying.

**Digits individually, twice.** "Nine one four" is transcribable by ear;
"nine hundred and fourteen" is not, and a six-digit number said once at speed is
not either. The repeat is unconditional rather than on request, because
answering a request would need us to listen.

**How the code reaches the call.** Plivo fetches an ``answer_url`` when the
callee picks up; it has no way to carry inline XML. Putting the code in that URL
would write it to every access log between here and the carrier, so the URL
carries a single-use random token and the code sits in Redis under it for five
minutes. The token is spent the first time it is read: a replay gets nothing.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL

#: How long the spoken code stays retrievable. Comfortably longer than a phone
#: takes to ring and be answered, and far shorter than the code's own life.
TOKEN_TTL_SECONDS = 300

_PREFIX = "voice_otp:"

#: What Plivo's <Speak> can pronounce, mapped from the language a customer
#: picked for their agent. Deliberately small and explicit: an unsupported code
#: passed through would be read in a voice that mangles the digits, and the
#: caller would blame the code rather than the accent.
#:
#: English (India) is the fallback because the digits are the message and
#: en-IN pronounces them in the accent an Indian caller expects. Languages Plivo
#: cannot speak — Telugu, Tamil, Kannada — fall back rather than fail; when they
#: matter, the upgrade is synthesising with Sarvam and <Play>ing the audio,
#: which this deliberately does not do yet.
PLIVO_LANGUAGES: dict[str, str] = {
    "en": "en-IN",
    "en-IN": "en-IN",
    "en-US": "en-US",
    "en-GB": "en-GB",
    "hi": "hi-IN",
    "hi-IN": "hi-IN",
}

DEFAULT_LANGUAGE = "en-IN"

#: Said before the digits, in the same language. Short on purpose — the caller
#: wants the number, not an introduction.
PROMPTS: dict[str, str] = {
    "en-IN": "Your Decibyl verification code is",
    "en-US": "Your Decibyl verification code is",
    "en-GB": "Your Decibyl verification code is",
    "hi-IN": "आपका Decibyl सत्यापन कोड है",
}

REPEAT: dict[str, str] = {
    "en-IN": "Once more.",
    "en-US": "Once more.",
    "en-GB": "Once more.",
    "hi-IN": "एक बार और।",
}


@dataclass(frozen=True)
class SpokenCode:
    code: str
    language: str


def resolve_language(language: str | None) -> str:
    """The Plivo language code to speak in, never raising on an unknown one."""
    if not language:
        return DEFAULT_LANGUAGE
    return PLIVO_LANGUAGES.get(
        language, PLIVO_LANGUAGES.get(language.split("-")[0], DEFAULT_LANGUAGE)
    )


async def _redis() -> aioredis.Redis:
    return aioredis.from_url(REDIS_URL, decode_responses=True)


async def stash(code: str, *, language: str | None) -> str:
    """Park the code for the answer URL to collect, and return its token."""
    token = secrets.token_urlsafe(24)
    client = await _redis()
    try:
        await client.set(
            _PREFIX + token,
            json.dumps({"code": code, "language": resolve_language(language)}),
            ex=TOKEN_TTL_SECONDS,
        )
    finally:
        await client.aclose()
    return token


async def claim(token: str) -> SpokenCode | None:
    """Read the code for this token, once.

    Deleting on read is what makes the token single-use: the URL reaches the
    carrier and whatever sits between us and it, and a token that could be
    replayed would let anyone who saw one hear the code.
    """
    client = await _redis()
    try:
        raw = await client.getdel(_PREFIX + token)
    finally:
        await client.aclose()

    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return SpokenCode(code=payload["code"], language=payload["language"])
    except (ValueError, KeyError):
        logger.error("Voice OTP token {} held something unreadable.", token[:6])
        return None


def _digits(code: str) -> str:
    """ "9 1 4 2 0 7" — spaced so it is read one digit at a time.

    Without this a synthesiser says "nine hundred and fourteen thousand", which
    is the same number and useless to somebody writing it down.
    """
    return " ".join(code)


def speak_xml(spoken: SpokenCode) -> str:
    """The Plivo XML that reads the code out twice and hangs up."""
    language = spoken.language
    prompt = PROMPTS.get(language, PROMPTS[DEFAULT_LANGUAGE])
    repeat = REPEAT.get(language, REPEAT[DEFAULT_LANGUAGE])
    digits = _digits(spoken.code)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f'    <Speak language="{language}">{prompt}</Speak>\n'
        f'    <Speak language="{language}">{digits}</Speak>\n'
        f'    <Speak language="{language}">{repeat}</Speak>\n'
        f'    <Speak language="{language}">{digits}</Speak>\n'
        "</Response>"
    )


def unavailable_xml() -> str:
    """What the carrier gets when the token has expired or been spent.

    Says something rather than hanging up silently: the person answered a call
    from us and deserves to know it was real and is over.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        '    <Speak language="en-IN">Sorry, this verification call has '
        "expired. Please request a new code.</Speak>\n"
        "</Response>"
    )
