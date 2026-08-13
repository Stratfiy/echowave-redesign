"""Getting a verification code to the person holding the phone.

Split from :mod:`verified_numbers` because the transport is the part that is
blocked on paperwork, and the rest of the feature should not be.

**The India problem, written down so nobody rediscovers it.** TRAI's TCCCPR
requires commercial SMS to Indian numbers to be registered on a DLT platform
before any of it flows: a Principal Entity (PAN/GST), a registered header such
as ``DCBYL``, and a registered content template with the variable part marked —
``Your Decibyl verification code is {#var#}``. Unregistered traffic is
**blocked by the operator**, not merely discouraged. The failure mode is the
nasty one: the code looks correct, Plivo accepts the request, and the message
never arrives.

So the channel is a seam rather than a hard-coded call:

* ``log`` — writes the code to the log. Development and tests only, and it
  refuses to run outside them.
* ``voice`` — calls the number and reads the code out. We are a voice platform;
  transactional voice does not carry the DLT template requirement that SMS
  does, and the thing a trial user is verifying is precisely that Decibyl can
  ring them.
* ``plivo_sms`` / ``twilio_sms`` — the conventional routes, once DLT
  registration exists.

**Changing carrier does not avoid DLT.** The requirement attaches to the
destination and to *your* Principal Entity, not to the carrier: Twilio, Plivo
and everyone else refuse or drop unregistered Indian A2P traffic alike. Both
are here so the choice is a config value once the paperwork lands, not so one
can be used to route around it.

Every sender returns rather than raises, for the same reason the follow-up
message path does: whether the code reached them is an outcome to report, not
an exception to unwind a request over.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from api.constants import (
    ENVIRONMENT,
    PLATFORM_PLIVO_AUTH_ID,
    PLATFORM_PLIVO_AUTH_TOKEN,
    PLATFORM_SMS_FROM_NUMBER,
    PLATFORM_TWILIO_ACCOUNT_SID,
    PLATFORM_TWILIO_AUTH_TOKEN,
    VERIFICATION_CHANNEL,
)


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    channel: str
    #: Shown to the user when ok is False. Says what to do, not what broke.
    error: str | None = None
    #: For support: the carrier's own id for the message or call.
    reference: str | None = None


def _body(code: str) -> str:
    """The message text.

    Must match the registered DLT template character for character once SMS is
    live — the operator matches on the template, and a message that differs by
    a full stop is rejected as an unregistered template.
    """
    return f"Your Decibyl verification code is {code}. It expires in 10 minutes."


async def _send_log(number: str, code: str) -> DeliveryResult:
    """Development only.

    Guarded rather than trusted. A deployment that fell back to this in
    production would tell every user "code sent" while sending nothing, and the
    only place the code would exist is a log line — which is both a broken
    feature and a credential in the wrong place.
    """
    if (ENVIRONMENT or "").lower() not in {"test", "local", "development", "dev"}:
        return DeliveryResult(
            ok=False,
            channel="log",
            error=(
                "Verification is not configured on this deployment. "
                "Set VERIFICATION_CHANNEL and the platform carrier credentials."
            ),
        )
    logger.warning(
        f"[dev] verification code for {number} is {code} — "
        "log channel, no message was sent"
    )
    return DeliveryResult(ok=True, channel="log")


async def _send_plivo_sms(number: str, code: str) -> DeliveryResult:
    """SMS on Decibyl's own Plivo account.

    Deliberately NOT the customer's telephony configuration. The account this
    feature exists for has no telephony configuration — that is the entire
    point — so resolving credentials the way the post-call follow-up does would
    fail for exactly the users it is meant to serve.
    """
    if not (PLATFORM_PLIVO_AUTH_ID and PLATFORM_PLIVO_AUTH_TOKEN):
        return DeliveryResult(
            ok=False,
            channel="plivo_sms",
            error="Verification is not configured on this deployment.",
        )
    if not PLATFORM_SMS_FROM_NUMBER:
        return DeliveryResult(
            ok=False,
            channel="plivo_sms",
            error="Verification is not configured on this deployment.",
        )

    from api.services.messaging.send import MessagingError, send_message

    try:
        result = await send_message(
            provider="plivo",
            credentials={
                "auth_id": PLATFORM_PLIVO_AUTH_ID,
                "auth_token": PLATFORM_PLIVO_AUTH_TOKEN,
            },
            to=number,
            from_=PLATFORM_SMS_FROM_NUMBER,
            body=_body(code),
        )
    except MessagingError as exc:
        logger.error(f"Verification SMS could not be sent: {exc}")
        return DeliveryResult(ok=False, channel="plivo_sms", error=str(exc))

    if not result.ok:
        # Worth reading the carrier's own words in the log: an unregistered DLT
        # template comes back from Plivo as a rejection here, and that is the
        # single most likely reason for this to fail in India.
        logger.warning(f"Carrier refused the verification SMS: {result.error}")
        return DeliveryResult(
            ok=False,
            channel="plivo_sms",
            error="The code could not be sent to that number. Try again shortly.",
        )

    return DeliveryResult(ok=True, channel="plivo_sms", reference=result.message_id)


async def _send_twilio_sms(number: str, code: str) -> DeliveryResult:
    """SMS on Decibyl's own Twilio account.

    Same shape as the Plivo branch and for the same reason — the customer's own
    telephony configuration is the wrong credential here, because the accounts
    this serves have none.

    Worth being clear that this is not a way around DLT. The registration is
    against the sending entity and the Indian destination, so Twilio drops
    unregistered A2P traffic exactly as Plivo does. Having both is about not
    being locked to one carrier once the paperwork exists.
    """
    if not (PLATFORM_TWILIO_ACCOUNT_SID and PLATFORM_TWILIO_AUTH_TOKEN):
        return DeliveryResult(
            ok=False,
            channel="twilio_sms",
            error="Verification is not configured on this deployment.",
        )
    if not PLATFORM_SMS_FROM_NUMBER:
        return DeliveryResult(
            ok=False,
            channel="twilio_sms",
            error="Verification is not configured on this deployment.",
        )

    from api.services.messaging.send import MessagingError, send_message

    try:
        result = await send_message(
            provider="twilio",
            credentials={
                "account_sid": PLATFORM_TWILIO_ACCOUNT_SID,
                "auth_token": PLATFORM_TWILIO_AUTH_TOKEN,
            },
            to=number,
            from_=PLATFORM_SMS_FROM_NUMBER,
            body=_body(code),
        )
    except MessagingError as exc:
        logger.error(f"Verification SMS could not be sent: {exc}")
        return DeliveryResult(ok=False, channel="twilio_sms", error=str(exc))

    if not result.ok:
        # Twilio error 21610 and the 30xxx delivery codes are where an
        # unregistered DLT template surfaces. The carrier's own words are the
        # useful ones, so they go in the log rather than being flattened.
        logger.warning(f"Carrier refused the verification SMS: {result.error}")
        return DeliveryResult(
            ok=False,
            channel="twilio_sms",
            error="The code could not be sent to that number. Try again shortly.",
        )

    return DeliveryResult(ok=True, channel="twilio_sms", reference=result.message_id)


async def _send_voice(number: str, code: str) -> DeliveryResult:
    """Call the number and read the code out.

    Not wired yet, and saying so is the point. `STATUS.md` records that outbound
    on the platform account has never completed a real call in this deployment —
    the dispatch path exists and is untested. Shipping this as though it worked
    would produce a verification flow that silently never rings, which is worse
    than one that says it is unavailable.

    What it needs: one real outbound call on the platform Plivo account to prove
    the path, then a short TTS script reading the digits individually with a
    repeat, because a six-digit number said once at speed is not transcribable
    by ear.
    """
    return DeliveryResult(
        ok=False,
        channel="voice",
        error=(
            "Voice verification is not enabled on this deployment yet. "
            "Ask support to verify this number."
        ),
    )


async def deliver_code(number: str, code: str) -> DeliveryResult:
    """Send the code by whichever channel this deployment is configured for."""
    channel = (VERIFICATION_CHANNEL or "log").strip().lower()
    if channel == "plivo_sms":
        return await _send_plivo_sms(number, code)
    if channel == "twilio_sms":
        return await _send_twilio_sms(number, code)
    if channel == "voice":
        return await _send_voice(number, code)
    if channel == "log":
        return await _send_log(number, code)

    logger.error(f"Unknown VERIFICATION_CHANNEL {channel!r}")
    return DeliveryResult(
        ok=False,
        channel=channel,
        error="Verification is not configured on this deployment.",
    )
