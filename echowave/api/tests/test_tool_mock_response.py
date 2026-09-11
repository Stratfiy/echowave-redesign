"""An agent can be finished and demonstrated before its backend exists.

The most common state of a voice agent is "done, except the API it calls has
not been built". Until a tool could answer from its own configuration, such an
agent could be built and never shown: the call reached a URL that refused or
did not resolve, the model was handed an error at the exact moment the
conversation became useful, and it died in front of whoever was watching.

That is not hypothetical here. Every HTTP tool on this account pointed at
``example.invalid`` -- validate_customer, otp_status, trigger_manual_otp,
book_appointment, check_slots, capture_lead -- so the Elock agent stopped dead
at the first step of its own flow during a live test.

The property that matters most is the second class below: a mocked call must
never be indistinguishable from a real one to anything downstream.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.services.workflow.tools.custom_tool import execute_http_tool


class MockToolModel:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _tool(**config):
    base = {
        "method": "POST",
        "url": "https://example.invalid/elock/validate",
        "timeout_ms": 5000,
    }
    base.update(config)
    return MockToolModel(
        tool_uuid="t-mock",
        name="validate_customer",
        description="Check the TT and invoice numbers",
        category="http_api",
        definition={"schema_version": 1, "type": "http_api", "config": base},
    )


@pytest.mark.asyncio
class TestItAnswersWithoutTheBackend:
    async def test_the_configured_body_comes_back_as_a_success(self):
        tool = _tool(mock_response={"valid": True, "trip_id": "TRP-4417"})

        result = await execute_http_tool(tool, {"tt_number": "TN20AB1234"})

        assert result["status"] == "success"
        assert result["data"] == {"valid": True, "trip_id": "TRP-4417"}

    async def test_no_request_is_made_at_all(self):
        """Not a request that is ignored -- no request. The URL in these tools
        does not resolve, so anything that reaches the network costs the
        caller the timeout before the agent can speak."""
        tool = _tool(mock_response={"valid": True})

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as client_class:
            await execute_http_tool(tool, {})

        client_class.assert_not_called()

    async def test_an_unreachable_url_no_longer_ends_the_conversation(self):
        """The Elock case exactly: example.invalid, and the flow continues."""
        tool = _tool(mock_response={"valid": True})

        result = await execute_http_tool(tool, {"tt_number": "TN20AB1234"})

        assert result["status"] == "success"


@pytest.mark.asyncio
class TestNobodyDownstreamCanMistakeItForReal:
    """The model is told it worked, so a mocked booking sounds exactly like a
    real one. That is right for a demo and a disaster if the transcript, the
    receipt or an audit cannot tell the two apart."""

    async def test_the_result_says_it_was_mocked(self):
        tool = _tool(mock_response={"booked": True})

        result = await execute_http_tool(tool, {})

        assert result["mocked"] is True

    async def test_a_real_call_is_not_marked_mocked(self):
        tool = _tool()

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as client_class:
            client = AsyncMock()
            response = Mock()
            response.status_code = 200
            response.json.return_value = {"booked": True}
            client.request.return_value = response
            client_class.return_value.__aenter__.return_value = client
            result = await execute_http_tool(_tool(url="https://api.example.com/x"), {})

        assert result["status"] == "success"
        assert "mocked" not in result


@pytest.mark.asyncio
class TestItStaysOutOfTheWayWhenNotConfigured:
    async def test_no_mock_means_the_url_is_called(self):
        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as client_class:
            client = AsyncMock()
            response = Mock()
            response.status_code = 200
            response.json.return_value = {"ok": True}
            client.request.return_value = response
            client_class.return_value.__aenter__.return_value = client
            await execute_http_tool(_tool(url="https://api.example.com/x"), {})

        client.request.assert_awaited_once()

    @pytest.mark.parametrize("empty", [None, "", [], "null"])
    async def test_anything_that_is_not_an_object_is_not_a_mock(self, empty):
        """A cleared field, or a string somebody typed, must not silently turn
        a live tool into one that never calls anything."""
        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as client_class:
            client = AsyncMock()
            response = Mock()
            response.status_code = 200
            response.json.return_value = {"ok": True}
            client.request.return_value = response
            client_class.return_value.__aenter__.return_value = client
            await execute_http_tool(
                _tool(url="https://api.example.com/x", mock_response=empty), {}
            )

        client.request.assert_awaited_once()

    async def test_an_empty_object_is_still_a_mock(self):
        """`{}` is a deliberate "answer with nothing and succeed", which is a
        real thing to want from a fire-and-forget endpoint."""
        tool = _tool(mock_response={})

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as client_class:
            result = await execute_http_tool(tool, {})

        client_class.assert_not_called()
        assert result["status"] == "success"
        assert result["mocked"] is True
