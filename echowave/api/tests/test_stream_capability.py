"""Who may attach to a call's audio.

The media socket is dialled back by the carrier, so its whole URL is supplied
by whoever connects. Before this, the only thing standing between an outsider
and live bidirectional audio on somebody else's call was guessing three small
integers — and the org-scoping behind it does not help, because the
organization id is one of the three.

These tests are about the property that closes that: a connection has to
present a capability minted for *that exact call*. A token that is merely
valid is not enough, because the interesting attack is not forging one — it is
holding a real one and pointing it at a different run.
"""

from __future__ import annotations

import os

import pytest

from api.services.telephony import stream_capability

requires_redis = pytest.mark.skipif(
    "REDIS_URL" not in os.environ,
    reason="Requires Redis (set REDIS_URL via .env.test)",
)

TRIPLE = {"workflow_id": 11, "organization_id": 22, "workflow_run_id": 33}


@requires_redis
class TestACapabilityGrantsOneCall:
    async def test_a_minted_token_verifies_for_its_own_run(self):
        token = await stream_capability.mint(**TRIPLE)
        assert token
        assert await stream_capability.verify(token, **TRIPLE) is True

    async def test_the_same_token_is_useless_on_another_run(self):
        """The whole point.

        A token reaches the carrier, its logs, and whatever sits between us and
        it. If holding one let you attach to any call, leaking one would be a
        platform-wide compromise rather than a replay of the call it was for.
        """
        token = await stream_capability.mint(**TRIPLE)

        assert (
            await stream_capability.verify(
                token, workflow_id=11, organization_id=22, workflow_run_id=34
            )
            is False
        )

    async def test_it_is_useless_for_another_organization(self):
        """The id this socket used to trust is the id an attacker types.

        Scoping the lookups by organization_id turns a *mistake* into a 4404.
        It does nothing about somebody choosing the number, which is why the
        token has to be bound to it rather than merely accompany it.
        """
        token = await stream_capability.mint(**TRIPLE)

        assert (
            await stream_capability.verify(
                token, workflow_id=11, organization_id=999, workflow_run_id=33
            )
            is False
        )

    async def test_it_survives_a_reconnect(self):
        """Deliberately not spent on first read.

        ``voice_otp`` deletes its token on read and should — that URL is
        fetched once. This one is dialled by a carrier that reconnects after a
        network blip, and a spent token would turn a recoverable drop into a
        dead call for somebody mid-sentence. The window is closed by the TTL
        instead.
        """
        token = await stream_capability.mint(**TRIPLE)

        assert await stream_capability.verify(token, **TRIPLE) is True
        assert await stream_capability.verify(token, **TRIPLE) is True


@requires_redis
class TestWhatIsRefused:
    async def test_no_token_is_refused(self):
        assert await stream_capability.verify(None, **TRIPLE) is False
        assert await stream_capability.verify("", **TRIPLE) is False

    async def test_a_token_nobody_minted_is_refused(self):
        assert await stream_capability.verify("not-a-real-token", **TRIPLE) is False


@requires_redis
class TestTheUrlCarriesIt:
    async def test_the_stream_url_carries_a_token_that_verifies(self):
        """One helper builds this URL now.

        It used to be written by hand in seven places — five provider stream
        elements and two inbound routes. Adding anything to it meant finding
        all seven, which is how six of them would have shipped without a token.
        """
        url = await stream_capability.stream_url(**TRIPLE)

        assert "/api/v1/telephony/ws/11/22/33" in url
        assert f"?{stream_capability.TOKEN_PARAM}=" in url

        token = url.split(f"?{stream_capability.TOKEN_PARAM}=")[1]
        assert await stream_capability.verify(token, **TRIPLE) is True

    async def test_two_calls_never_share_a_capability(self):
        first = await stream_capability.mint(**TRIPLE)
        second = await stream_capability.mint(**TRIPLE)

        assert first != second
