"""A Cloudonix inbound webhook must actually be from Cloudonix.

``verify_inbound_signature`` compared the ``x-cx-apikey`` header against the
configured bearer token, logged the verdict -- and then returned ``True``
regardless, behind a ``# TODO: update this post clarification from cloudonix``.

The comparison running and the result being discarded is what made it hard to
see. The logs say "validation failed" on a forged request and the request is
processed anyway, so the one signal that something is wrong reads like a
already-handled warning.

What the webhook does is what makes it matter: ``/inbound/run`` starts a call on
the organization it names. Anybody who could reach the endpoint could originate
calls billed to any account, with any caller id, into any workflow.

Cloudonix genuinely does authenticate with a shared header rather than a signed
digest -- that part of the docstring was right. So this is a header comparison,
done in constant time and actually returned.
"""

from __future__ import annotations

from loguru import logger

from api.services.telephony.providers.cloudonix.provider import CloudonixProvider

_SECRET = "cx-live-a0b1c2d3e4f5a6b7c8d9e0f1"


def _provider(bearer_token: str | None = _SECRET) -> CloudonixProvider:
    return CloudonixProvider(
        {
            "bearer_token": bearer_token,
            "domain_id": "acme.cloudonix.net",
            "application_name": "decibyl",
        }
    )


async def _verify(provider: CloudonixProvider, headers: dict) -> bool:
    return await provider.verify_inbound_signature(
        "https://api.decibyl.ai/api/v1/telephony/inbound/run",
        {"CallSid": "abc"},
        headers,
        "",
    )


async def test_the_matching_key_is_accepted():
    assert await _verify(_provider(), {"x-cx-apikey": _SECRET}) is True


async def test_a_wrong_key_is_refused():
    """The regression: this returned True and the call went through."""
    assert await _verify(_provider(), {"x-cx-apikey": "not-the-key"}) is False


async def test_a_missing_key_is_refused():
    assert await _verify(_provider(), {}) is False


async def test_an_empty_key_is_refused():
    assert await _verify(_provider(), {"x-cx-apikey": ""}) is False


async def test_an_unconfigured_provider_refuses_rather_than_accepting_anything():
    """No configured token is not "nothing to check against, so allow it".

    An organization mid-setup would otherwise have an endpoint that accepts
    every caller until somebody finishes filling in the form.
    """
    assert await _verify(_provider(bearer_token=None), {"x-cx-apikey": "any"}) is False
    assert await _verify(_provider(bearer_token=""), {"x-cx-apikey": "any"}) is False


async def test_the_header_is_read_case_insensitively():
    """HTTP header names are case-insensitive and Cloudonix's casing is not
    ours to depend on. Reading only the lowercase spelling would fail closed on
    a header change -- safe, but it would take inbound calling down silently."""
    assert await _verify(_provider(), {"X-CX-APIKey": _SECRET}) is True


async def test_a_rejection_does_not_write_the_expected_key_into_the_logs():
    """The failure branch logged the last eight characters of the real token.

    A forged request is exactly the way an attacker gets that line written, and
    anyone who can read logs then holds a fragment of the credential.

    A loguru sink rather than ``caplog``: this module logs through loguru, and
    ``caplog`` only sees the standard library, so a caplog assertion here would
    pass on an empty string and prove nothing.
    """
    written: list[str] = []
    sink_id = logger.add(written.append, level="DEBUG")
    try:
        await _verify(_provider(), {"x-cx-apikey": "wrong"})
    finally:
        logger.remove(sink_id)

    captured = "".join(written)
    assert captured, "nothing was logged, so this test proves nothing"
    assert _SECRET not in captured
    assert _SECRET[-8:] not in captured
