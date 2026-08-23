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


#: ENVIRONMENTs in which the `log` channel is allowed to stand in for delivery.
DEV_ENVIRONMENTS = frozenset({"test", "local", "development", "dev"})


def is_deliverable() -> bool:
    """Whether a code can actually reach anyone on this deployment.

    Asked *before* a caller tells someone to go and verify a number. The `log`
    channel refuses to run outside a dev environment, so on a real deployment
    that has configured nothing, "enter the code we send you" instructs the
    customer to complete a step that cannot be completed — and they discover
    that only after following it, from a 502 on the verify screen.

    Deliberately a cheap configuration read rather than a probe: the question is
    "is this deployment set up to send", not "is the carrier up right now". A
    carrier having a bad minute is a delivery failure and reports itself as one.
    """
    channel = (VERIFICATION_CHANNEL or "log").strip().lower()
    if channel == "log":
        return (ENVIRONMENT or "").lower() in DEV_ENVIRONMENTS
    return channel in {"plivo_sms", "twilio_sms", "voice"}


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
    if (ENVIRONMENT or "").lower() not in DEV_ENVIRONMENTS:
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


async def _send_voice(
    number: str, code: str, *, language: str | None = None
) -> DeliveryResult:
    """Call the number and read the code out, twice.

    The channel that does not wait on DLT. Transactional voice carries no
    registered-template requirement, and the thing a trial user is verifying is
    precisely that Decibyl can ring them.

    Dials from the shared outbound pool — Decibyl's own numbers — so an account
    with no carrier configuration of its own can still be verified, which is the
    entire point of verifying a trial user. No pool, no channel: this reports
    that rather than pretending, because a verification flow that silently never
    rings is worse than one that says it is unavailable.

    The code goes into Redis under a single-use token and the token goes in the
    answer URL. Plivo cannot carry inline XML, and the code must not travel in a
    URL that every access log on the way will keep.
    """
    from api.services.telephony import shared_outbound, voice_otp
    from api.utils.common import get_backend_endpoints

    try:
        _configuration_id, provider, from_numbers = await shared_outbound.get_provider()
    except shared_outbound.NoSharedNumber as exc:
        logger.warning("Voice verification has no number to call from: {}", exc)
        return DeliveryResult(
            ok=False,
            channel="voice",
            error=(
                "Voice verification is not available on this deployment yet — "
                "no calling number is configured. Ask support to verify this "
                "number."
            ),
        )

    token = await voice_otp.stash(code, language=language)
    backend_endpoint, _ = await get_backend_endpoints()
    answer_url = f"{backend_endpoint}/api/v1/telephony/verification/voice/{token}"

    try:
        result = await provider.initiate_call(
            to_number=number,
            webhook_url=answer_url,
            from_number=from_numbers[0] if from_numbers else None,
        )
    except Exception as exc:  # noqa: BLE001 -- an outcome to report, see module docstring
        logger.error("Voice verification call to {} failed: {}", number, exc)
        return DeliveryResult(
            ok=False,
            channel="voice",
            error="We could not place the verification call. Try again shortly.",
        )

    return DeliveryResult(
        ok=True,
        channel="voice",
        reference=getattr(result, "call_id", None),
    )


async def deliver_code(
    number: str, code: str, *, language: str | None = None
) -> DeliveryResult:
    """Send the code by whichever channel this deployment is configured for.

    ``language`` is the caller's preference for the spoken channel and is
    ignored by the others: an SMS body has to match the registered DLT template
    character for character, so it is not a thing a user gets to pick.
    """
    channel = (VERIFICATION_CHANNEL or "log").strip().lower()
    if channel == "plivo_sms":
        return await _send_plivo_sms(number, code)
    if channel == "twilio_sms":
        return await _send_twilio_sms(number, code)
    if channel == "voice":
        return await _send_voice(number, code, language=language)
    if channel == "log":
        return await _send_log(number, code)

    logger.error(f"Unknown VERIFICATION_CHANNEL {channel!r}")
    return DeliveryResult(
        ok=False,
        channel=channel,
        error="Verification is not configured on this deployment.",
    )
