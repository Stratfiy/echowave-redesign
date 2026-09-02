"""Plivo warm transfer: the destination leg, the caller leg, and the XML.

Plivo has no inline-XML parameter, so a transfer here is three moving parts
rather than Twilio's one API call — the destination is dialled at an
``answer_url``, the caller's live leg is redirected to a second URL, and both
URLs serve conference XML from this app. Each part is asserted separately
because each fails differently and only one of them is visible in a log.

The behaviours that carry real cost if they regress:

* **The caller is not hung up on a transfer.** Upstream's serializer hangs up
  on any ``EndFrame`` when ``auto_hang_up`` is set. A transfer ends the
  pipeline, not the call — taking that default drops the caller a moment
  before the human picks up, and the customer experiences it as the agent
  cutting them off on request.
* **The briefing plays before the bridge, on the destination's leg only.**
  Reverse those two elements and the caller hears you describing them.
* **The destination is dialled once.** A second EndFrame arriving while the
  redirect is in flight must not ring the human twice.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import EndFrame
from pipecat.utils.enums import EndTaskReason

from api.services.telephony.providers.plivo.routes import (
    handle_plivo_transfer_bridge,
    handle_plivo_transfer_caller,
)
from api.services.telephony.providers.plivo.serializers import PlivoFrameSerializer
from api.services.telephony.providers.plivo.strategies import PlivoConferenceStrategy


def _xml(response) -> str:
    return response.body.decode()


class _Req:
    """Just enough of a Request for the bridge endpoint's query lookup."""

    def __init__(self, **params):
        self.query_params = params


# ---------------------------------------------------------------------------
# The XML both legs are pointed at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_xml_speaks_the_briefing_before_joining():
    body = _xml(
        await handle_plivo_transfer_bridge(
            "conf-abc", _Req(briefing="Caller wants a refund.")
        )
    )

    assert "Caller wants a refund." in body
    assert "conf-abc" in body
    # Order is the mechanism, not a preference.
    assert body.index("<Speak>") < body.index("<Conference")


@pytest.mark.asyncio
async def test_bridge_xml_ends_the_conference_when_the_human_leaves():
    body = _xml(await handle_plivo_transfer_bridge("conf-abc", _Req()))
    assert 'endConferenceOnExit="true"' in body


@pytest.mark.asyncio
async def test_bridge_xml_escapes_a_briefing_that_would_break_the_document():
    # The briefing is operator-supplied text arriving through a query string.
    # Unescaped, a stray ampersand or bracket yields XML Plivo cannot parse,
    # and Plivo's response to unparseable XML is to drop the leg.
    body = _xml(
        await handle_plivo_transfer_bridge(
            "conf-abc", _Req(briefing="Billing & <urgent>")
        )
    )

    assert "&amp;" in body and "&lt;urgent&gt;" in body
    assert "<urgent>" not in body


@pytest.mark.asyncio
async def test_caller_xml_is_silent_and_does_not_open_the_conference():
    body = _xml(await handle_plivo_transfer_caller("conf-abc"))

    # No second announcement — the caller has already been told.
    assert "<Speak>" not in body
    # A caller arriving first waits rather than starting an empty room.
    assert 'startConferenceOnEnter="false"' in body
    assert "conf-abc" in body


# ---------------------------------------------------------------------------
# The caller's leg
# ---------------------------------------------------------------------------


def _strategy_context():
    return {"call_id": "call-uuid-1", "auth_id": "MA123", "auth_token": "secret"}


@pytest.mark.asyncio
async def test_caller_leg_is_redirected_to_the_conference():
    strategy = PlivoConferenceStrategy()
    context = SimpleNamespace(transfer_id="t-1", conference_name="conf-abc")

    posted = {}

    class _Resp:
        status = 202

        async def text(self):
            return "{}"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, url, json=None, auth=None):
            posted["url"] = url
            posted["json"] = json
            return _Resp()

    with (
        patch.object(
            strategy, "_find_transfer_context_for_call", AsyncMock(return_value=context)
        ),
        patch.object(strategy, "_cleanup_transfer_context", AsyncMock()),
        patch(
            "api.services.telephony.providers.plivo.strategies.get_backend_endpoints",
            AsyncMock(return_value=("https://api.example.com", "")),
        ),
        patch(
            "api.services.telephony.providers.plivo.strategies.aiohttp.ClientSession",
            lambda *a, **k: _Session(),
        ),
    ):
        assert await strategy.execute_transfer(_strategy_context()) is True

    assert posted["url"].endswith("/Call/call-uuid-1/")
    # Without legs=aleg Plivo redirects both legs, which on a call with no
    # b-leg is a silent no-op rather than an error.
    assert posted["json"]["legs"] == "aleg"
    assert posted["json"]["aleg_url"].endswith(
        "/api/v1/telephony/plivo/transfer-caller/conf-abc"
    )


@pytest.mark.asyncio
async def test_transfer_fails_cleanly_when_no_context_exists():
    strategy = PlivoConferenceStrategy()
    cleanup = AsyncMock()

    with (
        patch.object(
            strategy, "_find_transfer_context_for_call", AsyncMock(return_value=None)
        ),
        patch.object(strategy, "_cleanup_transfer_context", cleanup),
    ):
        assert await strategy.execute_transfer(_strategy_context()) is False


@pytest.mark.asyncio
async def test_transfer_context_is_cleaned_up_even_when_plivo_rejects():
    # A context left behind is resolved by the *next* transfer on this call,
    # which then redirects into a conference nobody is in.
    strategy = PlivoConferenceStrategy()
    context = SimpleNamespace(transfer_id="t-1", conference_name="conf-abc")
    cleanup = AsyncMock()

    class _Resp:
        status = 404

        async def text(self):
            return "no such call"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp()

    with (
        patch.object(
            strategy, "_find_transfer_context_for_call", AsyncMock(return_value=context)
        ),
        patch.object(strategy, "_cleanup_transfer_context", cleanup),
        patch(
            "api.services.telephony.providers.plivo.strategies.get_backend_endpoints",
            AsyncMock(return_value=("https://api.example.com", "")),
        ),
        patch(
            "api.services.telephony.providers.plivo.strategies.aiohttp.ClientSession",
            lambda *a, **k: _Session(),
        ),
    ):
        assert await strategy.execute_transfer(_strategy_context()) is False

    cleanup.assert_awaited_once_with("t-1")


# ---------------------------------------------------------------------------
# The serializer, which decides whether the caller survives the teardown
# ---------------------------------------------------------------------------


def _serializer(strategy=None):
    return PlivoFrameSerializer(
        stream_id="stream-1",
        call_id="call-uuid-1",
        auth_id="MA123",
        auth_token="secret",
        transfer_strategy=strategy,
    )


@pytest.mark.asyncio
async def test_transfer_teardown_runs_the_strategy_and_does_not_hang_up():
    strategy = AsyncMock()
    strategy.execute_transfer = AsyncMock(return_value=True)
    serializer = _serializer(strategy)

    with patch.object(
        PlivoFrameSerializer, "_hang_up_call", AsyncMock()
    ) as hang_up:
        await serializer.serialize(
            EndFrame(reason=EndTaskReason.TRANSFER_CALL.value)
        )

    strategy.execute_transfer.assert_awaited_once()
    # The whole point: the caller is going to a conference, not to silence.
    hang_up.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_second_end_frame_does_not_transfer_twice():
    strategy = AsyncMock()
    strategy.execute_transfer = AsyncMock(return_value=True)
    serializer = _serializer(strategy)

    with patch.object(PlivoFrameSerializer, "_hang_up_call", AsyncMock()):
        await serializer.serialize(EndFrame(reason=EndTaskReason.TRANSFER_CALL.value))
        await serializer.serialize(EndFrame(reason=EndTaskReason.TRANSFER_CALL.value))

    assert strategy.execute_transfer.await_count == 1


@pytest.mark.asyncio
async def test_an_ordinary_teardown_still_hangs_up():
    # Everything that is not a transfer must keep the old behaviour.
    serializer = _serializer(AsyncMock())

    with patch.object(
        PlivoFrameSerializer, "_hang_up_call", AsyncMock()
    ) as hang_up:
        await serializer.serialize(EndFrame())

    hang_up.assert_awaited_once()
