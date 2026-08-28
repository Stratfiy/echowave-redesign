"""Every carrier we hand a media socket URL to gets one it can actually use.

The socket refuses a connection that presents no capability
(``TELEPHONY_WS_REQUIRE_TOKEN``, on by default). That check is only half a
mechanism: it is worth nothing if a URL goes out without a token, and worse
than nothing, because the failure lands *after the callee has answered*. The
call rings, the customer picks up, the carrier opens the media socket, has the
handshake refused, and hangs up. From the outside that is "the call ends when
I answer", with nothing in the call's own record naming the cause.

That is exactly what shipped for Telnyx. When the token was introduced, seven
call sites were moved onto the shared builder — five markup stream elements and
two inbound routes. Telnyx has an eighth: it streams inline with the dial
request rather than from a markup response, so it is not reached by looking at
webhook handlers, and it kept building the URL by hand.

So these tests cover the property rather than the instance: no module outside
the builder spells this URL, and the one provider that used to gets checked
directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.services.telephony import stream_capability
from api.services.telephony.providers.telnyx.provider import TelnyxProvider

API_ROOT = Path(__file__).resolve().parents[1]

#: The path component of the media socket. Deliberately written out here rather
#: than imported: this test is about nobody *else* writing it out.
MEDIA_SOCKET_PATH = "/api/v1/telephony/ws"

CREDENTIALS = {
    "api_key": "KEY-not-real",
    "connection_id": "1234567890",
    "from_numbers": ["+911111111111"],
}

TRIPLE = {"workflow_id": 11, "organization_id": 22, "workflow_run_id": 33}


class TestOnlyTheBuilderSpellsTheUrl:
    def test_no_other_module_writes_the_media_socket_path(self):
        """One builder, or the token is optional in practice.

        A provider that assembles this URL itself is a provider whose calls die
        on answer, and the seven-out-of-eight hit rate the first time says
        grepping for it by hand is not a reliable check. So the codebase makes
        the assertion instead.

        If you are here because this failed: you almost certainly want
        ``stream_capability.stream_url(...)``, which returns the same URL with a
        capability appended.
        """
        offenders = {
            path
            for path in API_ROOT.rglob("*.py")
            if "tests" not in path.parts
            and MEDIA_SOCKET_PATH in path.read_text(encoding="utf-8")
        }

        assert offenders == {API_ROOT / "services/telephony/stream_capability.py"}, (
            "The media socket URL is spelled outside the builder. Any carrier "
            "handed that URL gets no capability, and its calls are refused "
            f"after answer. Offending files: {sorted(map(str, offenders))}"
        )


@pytest.mark.asyncio
class TestTelnyxDialsAnAuthenticatedSocket:
    """Telnyx is the inline-streaming case, and the one that regressed."""

    @staticmethod
    def _stub_transport(monkeypatch, seen: dict):
        import aiohttp

        def _post(self, url, **kwargs):
            seen.update(kwargs.get("json") or {})

            class _Response:
                status = 200

                async def json(self):
                    return {
                        "data": {
                            "call_control_id": "cc-1",
                            "call_leg_id": "leg-1",
                            "call_session_id": "sess-1",
                        }
                    }

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_a):
                    return False

            return _Response()

        monkeypatch.setattr(aiohttp.ClientSession, "post", _post)

    async def test_the_dial_payload_carries_a_capability(self, monkeypatch):
        """The regression, stated as the payload Telnyx is actually sent.

        Asserted on ``stream_url`` in the dial body rather than on the socket's
        answer, because that body is the last place we control: once Telnyx has
        it, a URL without a token is a call that will be dropped after pickup
        and there is nothing further to check.
        """
        monkeypatch.setattr(
            stream_capability,
            "get_backend_endpoints",
            _fake_endpoints,
        )
        monkeypatch.setattr(stream_capability, "mint", _mint_fixed_token)

        seen: dict = {}
        self._stub_transport(monkeypatch, seen)

        await TelnyxProvider(dict(CREDENTIALS)).initiate_call(
            to_number="+919999999999",
            webhook_url="https://example.test/api/v1/telephony/telnyx/webhook",
            workflow_run_id=TRIPLE["workflow_run_id"],
            from_number=CREDENTIALS["from_numbers"][0],
            workflow_id=TRIPLE["workflow_id"],
            organization_id=TRIPLE["organization_id"],
        )

        stream_url = seen.get("stream_url", "")
        assert f"{MEDIA_SOCKET_PATH}/11/22/33" in stream_url, (
            f"Telnyx was dialled with an unexpected media socket: {stream_url!r}"
        )
        assert stream_url.endswith(f"?{stream_capability.TOKEN_PARAM}=fixed-token"), (
            "Telnyx was dialled with a media socket URL carrying no capability; "
            f"the socket will refuse it after answer: {stream_url!r}"
        )

    async def test_it_refuses_to_dial_without_the_ids_to_mint_for(self, monkeypatch):
        """No run means no capability, and no capability means no call.

        The ids arrive through ``**kwargs``, so a caller that omits them used to
        produce a URL reading ``/ws/None/None/None`` — dialled, answered, and
        then dropped. Refusing is not a new failure; it is the existing one,
        raised where it can be reported.
        """
        seen: dict = {}
        self._stub_transport(monkeypatch, seen)

        with pytest.raises(ValueError, match="workflow_id"):
            await TelnyxProvider(dict(CREDENTIALS)).initiate_call(
                to_number="+919999999999",
                webhook_url="https://example.test/hook",
                workflow_run_id=None,
                from_number=CREDENTIALS["from_numbers"][0],
            )

        assert not seen, "A call was placed despite having no run to stream to"


@pytest.mark.asyncio
class TestAUrlIsNeverHandedOutThatCannotConnect:
    """What happens when Redis is down decides which failure the customer gets.

    Returning a token-less URL looks like graceful degradation and is the
    opposite: with the socket requiring a token, that call is placed, rings, is
    answered, and dies. The degraded-but-working version of this is the escape
    hatch, and it has to be switched on deliberately for the socket to accept
    the URL at the other end.
    """

    async def test_it_raises_when_a_token_is_required_and_cannot_be_minted(
        self, monkeypatch
    ):
        monkeypatch.setattr(stream_capability, "get_backend_endpoints", _fake_endpoints)
        monkeypatch.setattr(stream_capability, "mint", _mint_nothing)
        monkeypatch.setattr(stream_capability, "TELEPHONY_WS_REQUIRE_TOKEN", True)

        with pytest.raises(stream_capability.StreamCapabilityUnavailable):
            await stream_capability.stream_url(**TRIPLE)

    async def test_the_escape_hatch_still_returns_a_usable_url(self, monkeypatch):
        """With the check off, a token-less URL connects, so hand it over."""
        monkeypatch.setattr(stream_capability, "get_backend_endpoints", _fake_endpoints)
        monkeypatch.setattr(stream_capability, "mint", _mint_nothing)
        monkeypatch.setattr(stream_capability, "TELEPHONY_WS_REQUIRE_TOKEN", False)

        url = await stream_capability.stream_url(**TRIPLE)

        assert url.endswith(f"{MEDIA_SOCKET_PATH}/11/22/33")
        assert f"{stream_capability.TOKEN_PARAM}=" not in url


async def _fake_endpoints():
    return "https://api.example.test", "wss://api.example.test"


async def _mint_fixed_token(**_kwargs):
    return "fixed-token"


async def _mint_nothing(**_kwargs):
    return None
