"""Placing the verification call.

The seam between "we issued a code" and "a phone rang". Three things matter
here and none of them is the XML, which `test_voice_otp` covers:

* the call is dialled from Decibyl's own numbers, not the customer's carrier —
  the account this feature exists for has no carrier configuration, which is
  the entire reason it cannot verify itself any other way
* with no shared number the answer is a refusal the user can act on, not a
  crash and not a cheerful "code sent" for a call that never happened
* the code never appears in the answer URL
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.services.telephony import shared_outbound, verification_sender


class _Provider:
    def __init__(self):
        self.calls = []

    async def initiate_call(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(call_id="CALL-1")


@pytest.fixture
def provider(monkeypatch):
    fake = _Provider()

    async def _get_provider():
        return 7, fake, ["+919000000001"]

    monkeypatch.setattr(shared_outbound, "get_provider", _get_provider)

    async def _endpoints():
        return "https://api.example.com", "wss://api.example.com"

    monkeypatch.setattr(
        "api.utils.common.get_backend_endpoints", _endpoints, raising=True
    )
    return fake


@pytest.mark.asyncio
class TestDiallingTheCode:
    async def test_it_calls_the_number(self, provider):
        result = await verification_sender._send_voice("+919876543210", "914207")

        assert result.ok
        assert result.channel == "voice"
        assert provider.calls[0]["to_number"] == "+919876543210"

    async def test_it_dials_from_the_shared_pool(self, provider):
        """Not the customer's carrier. An account with no telephony
        configuration is precisely who this is for."""
        await verification_sender._send_voice("+919876543210", "914207")

        assert provider.calls[0]["from_number"] == "+919000000001"

    async def test_the_carrier_reference_is_kept(self, provider):
        """Support's only handle on "the call never came"."""
        result = await verification_sender._send_voice("+919876543210", "914207")

        assert result.reference == "CALL-1"

    async def test_the_code_is_not_in_the_answer_url(self, provider):
        """The URL reaches Plivo and every proxy and access log on the way. The
        code goes in Redis under a single-use token instead."""
        await verification_sender._send_voice("+919876543210", "914207")

        assert "914207" not in provider.calls[0]["webhook_url"]

    async def test_the_answer_url_carries_a_claimable_token(self, provider):
        """End to end through Redis: what the carrier will fetch resolves to
        the code we issued."""
        from api.services.telephony import voice_otp

        await verification_sender._send_voice("+919876543210", "914207", language="hi")
        token = provider.calls[0]["webhook_url"].rsplit("/", 1)[-1]

        spoken = await voice_otp.claim(token)
        assert spoken.code == "914207"
        assert spoken.language == "hi-IN"


@pytest.mark.asyncio
class TestWhenItCannotCall:
    async def test_no_shared_number_is_a_refusal_not_a_crash(self, monkeypatch):
        """A deployment where staff have not put a number in the pool. The user
        must be told, because the code was already issued and they will
        otherwise wait for a call that is not coming."""

        async def _none():
            raise shared_outbound.NoSharedNumber("nothing in the pool")

        monkeypatch.setattr(shared_outbound, "get_provider", _none)

        result = await verification_sender._send_voice("+919876543210", "914207")

        assert not result.ok
        assert result.error and "support" in result.error.lower()

    async def test_a_carrier_failure_is_reported_without_its_internals(
        self, monkeypatch
    ):
        """Plivo's rejection text is for the log, not for a user who can only
        act on "try again"."""

        class _Broken(_Provider):
            async def initiate_call(self, **kwargs):
                raise RuntimeError("Plivo said 401 auth_id invalid")

        async def _get_provider():
            return 7, _Broken(), ["+919000000001"]

        monkeypatch.setattr(shared_outbound, "get_provider", _get_provider)

        async def _endpoints():
            return "https://api.example.com", "wss://api.example.com"

        monkeypatch.setattr("api.utils.common.get_backend_endpoints", _endpoints)

        result = await verification_sender._send_voice("+919876543210", "914207")

        assert not result.ok
        assert "auth_id" not in (result.error or "")
