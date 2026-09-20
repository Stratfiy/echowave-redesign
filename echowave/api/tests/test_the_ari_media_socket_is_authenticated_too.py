"""The ARI media socket must present a capability, like every other one.

``/api/v1/telephony/ws/{workflow_id}/{organization_id}/{workflow_run_id}``
refuses a connection that cannot present a stream capability, because the three
ids in the path are supplied by whoever connects and three small integers are
not a secret. ``/api/v1/telephony/ws/ari`` took the same three ids from the
query string and checked nothing at all.

``stream_capability`` said ARI was "deliberately not covered ... the Asterisk
behind it is the customer's own machine on the customer's own network". The
first half is true and the second is about where Asterisk sits, not about who
can reach *us*. This route is mounted on the same public API as its sibling, so
the argument never applied to the thing that needed defending: anyone able to
reach the endpoint could guess a triple and attach to live, bidirectional audio
on somebody else's call.

Asterisk can carry the token. The externalMedia dial string already appends the
three ids as query params through ``v()``, so a fourth costs one more field.
"""

from __future__ import annotations

import pytest

from api.services.telephony import ari_manager, stream_capability

TRIPLE = {"workflow_id": 11, "organization_id": 22, "workflow_run_id": 33}


class TestAsteriskIsHandedACapability:
    async def test_the_dial_string_carries_a_token(self, monkeypatch):
        """Without this the route's check has nothing to accept, and every ARI
        call is answered and then dropped at the socket."""
        minted: dict = {}

        async def _mint(**kwargs):
            minted.update(kwargs)
            return "tok-abc123"

        monkeypatch.setattr(stream_capability, "mint", _mint)
        transport_data = await ari_manager.ARIConnection._external_media_transport_data(
            workflow_id="11", organization_id=22, workflow_run_id="33"
        )

        assert f"{stream_capability.TOKEN_PARAM}=tok-abc123" in transport_data
        assert minted == TRIPLE

    async def test_the_token_is_minted_for_the_run_it_will_be_used_on(
        self, monkeypatch
    ):
        """A capability is bound to its triple, so minting it for anything else
        produces a token the socket will correctly refuse."""
        minted: dict = {}

        async def _mint(**kwargs):
            minted.update(kwargs)
            return "tok-abc123"

        monkeypatch.setattr(stream_capability, "mint", _mint)
        await ari_manager.ARIConnection._external_media_transport_data(
            workflow_id="7", organization_id=8, workflow_run_id="9"
        )
        assert minted == {
            "workflow_id": 7,
            "organization_id": 8,
            "workflow_run_id": 9,
        }

    async def test_the_dial_string_stays_parseable(self, monkeypatch):
        """``v(a=1,b=2)`` is comma and paren delimited. A token containing
        either would truncate the dial string, and Asterisk would connect with
        some of the routing params missing rather than failing outright."""

        async def _mint(**kwargs):
            return "tok-with_urlsafe-chars_123"

        monkeypatch.setattr(stream_capability, "mint", _mint)
        transport_data = await ari_manager.ARIConnection._external_media_transport_data(
            workflow_id="11", organization_id=22, workflow_run_id="33"
        )

        assert transport_data.startswith("v(") and transport_data.endswith(")")
        fields = dict(f.split("=", 1) for f in transport_data[2:-1].split(","))
        assert fields == {
            "workflow_id": "11",
            "organization_id": "22",
            "workflow_run_id": "33",
            stream_capability.TOKEN_PARAM: "tok-with_urlsafe-chars_123",
        }

    async def test_an_unmintable_capability_stops_the_call_rather_than_placing_it(
        self, monkeypatch
    ):
        """The same trade its sibling makes. A call placed with a URL the
        socket will refuse rings, is answered, and dies when media should
        start -- which reads as "the call ends when I pick up" and leaves
        nothing in the call record saying why. Failing here puts the error
        where somebody can report it.
        """

        async def _mint(**kwargs):
            return None

        monkeypatch.setattr(stream_capability, "mint", _mint)
        monkeypatch.setattr(ari_manager, "TELEPHONY_WS_REQUIRE_TOKEN", True)

        with pytest.raises(stream_capability.StreamCapabilityUnavailable):
            await ari_manager.ARIConnection._external_media_transport_data(
                workflow_id="11", organization_id=22, workflow_run_id="33"
            )

    async def test_with_the_escape_hatch_off_the_call_still_goes_out(self, monkeypatch):
        """TELEPHONY_WS_REQUIRE_TOKEN=false is the documented degraded mode:
        the socket accepts an unauthenticated connection, so a token-less dial
        string is the working call the hatch exists to allow."""

        async def _mint(**kwargs):
            return None

        monkeypatch.setattr(stream_capability, "mint", _mint)
        monkeypatch.setattr(ari_manager, "TELEPHONY_WS_REQUIRE_TOKEN", False)

        transport_data = await ari_manager.ARIConnection._external_media_transport_data(
            workflow_id="11", organization_id=22, workflow_run_id="33"
        )
        # Parsed rather than substring-matched: TOKEN_PARAM is "t", which
        # appears inside "workflow_id" and would make this assertion pass for
        # the wrong reason.
        fields = dict(f.split("=", 1) for f in transport_data[2:-1].split(","))
        assert stream_capability.TOKEN_PARAM not in fields
        assert fields["workflow_run_id"] == "33"


class TestTheSocketChecksIt:
    """The route half. A token nobody verifies is decoration."""

    @staticmethod
    def _websocket(params: dict):
        class _FakeWebSocket:
            def __init__(self):
                self.query_params = params
                self.accepted = False
                self.closed_with = None

            async def accept(self, subprotocol=None):
                self.accepted = True

            async def close(self, code=None, reason=None):
                self.closed_with = (code, reason)

        return _FakeWebSocket()

    async def test_a_connection_with_no_capability_is_refused(self, monkeypatch):
        from api.routes import telephony as telephony_routes

        async def _verify(token, **kwargs):
            return False

        monkeypatch.setattr(stream_capability, "verify", _verify)
        monkeypatch.setattr(telephony_routes, "TELEPHONY_WS_REQUIRE_TOKEN", True)

        websocket = self._websocket(
            {"workflow_id": "11", "organization_id": "22", "workflow_run_id": "33"}
        )
        await telephony_routes.websocket_ari_endpoint(websocket)

        assert websocket.accepted is False, (
            "the handshake was accepted before the capability was checked"
        )
        assert websocket.closed_with == (4401, "Unauthorized")

    async def test_a_valid_capability_is_accepted(self, monkeypatch):
        from api.routes import telephony as telephony_routes

        seen: dict = {}

        async def _verify(token, **kwargs):
            seen["token"] = token
            seen.update(kwargs)
            return True

        async def _handle(websocket, workflow_id, organization_id, workflow_run_id):
            seen["handled"] = (workflow_id, organization_id, workflow_run_id)

        monkeypatch.setattr(stream_capability, "verify", _verify)
        monkeypatch.setattr(telephony_routes, "_handle_telephony_websocket", _handle)

        websocket = self._websocket(
            {
                "workflow_id": "11",
                "organization_id": "22",
                "workflow_run_id": "33",
                stream_capability.TOKEN_PARAM: "tok-abc123",
            }
        )
        await telephony_routes.websocket_ari_endpoint(websocket)

        assert websocket.accepted is True
        assert seen["token"] == "tok-abc123"
        # Verified against the triple in the URL, so a token minted for another
        # run cannot be replayed onto this one.
        assert seen["workflow_id"] == 11
        assert seen["organization_id"] == 22
        assert seen["workflow_run_id"] == 33
        assert seen["handled"] == (11, 22, 33)

    async def test_the_media_subprotocol_survives_the_new_check(self, monkeypatch):
        """chan_websocket sends Sec-WebSocket-Protocol: media and requires it
        echoed back. Adding a gate in front of accept() is exactly where that
        argument gets dropped."""
        from api.routes import telephony as telephony_routes

        accepted_with: dict = {}

        async def _verify(token, **kwargs):
            return True

        async def _handle(*args, **kwargs):
            return None

        monkeypatch.setattr(stream_capability, "verify", _verify)
        monkeypatch.setattr(telephony_routes, "_handle_telephony_websocket", _handle)

        websocket = self._websocket(
            {
                "workflow_id": "11",
                "organization_id": "22",
                "workflow_run_id": "33",
                stream_capability.TOKEN_PARAM: "tok-abc123",
            }
        )

        async def _accept(subprotocol=None):
            accepted_with["subprotocol"] = subprotocol
            websocket.accepted = True

        websocket.accept = _accept
        await telephony_routes.websocket_ari_endpoint(websocket)

        assert accepted_with["subprotocol"] == "media"

    async def test_missing_routing_params_are_still_refused_before_anything_else(
        self, monkeypatch
    ):
        from api.routes import telephony as telephony_routes

        websocket = self._websocket({"workflow_id": "11"})
        await telephony_routes.websocket_ari_endpoint(websocket)

        assert websocket.accepted is False
        assert websocket.closed_with[0] == 4400
