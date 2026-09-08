"""A tool action that failed must not reach the model as one that worked.

`execute_http_tool` returned ``{"status": "success"}`` for every response the
server managed to send. The status code was in the dict, but the field the model
reads said success, so a 401 from an expired token, a 404 from a wrong Zoho
datacentre, and a 500 from the vendor were all indistinguishable from a booking
that went through.

The consequence is not a log line. It is the agent telling the caller their
appointment is confirmed, the call ending, and nobody finding out until the
person arrives at a clinic that has no record of them. The ready-made CRM tools
make this path load-bearing, which is what turned a latent bug into a live one.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.services.workflow.tools.custom_tool import execute_http_tool


class MockToolModel:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _tool():
    return MockToolModel(
        tool_uuid="t-1",
        name="Book Appointment",
        description="Book an appointment",
        category="http_api",
        definition={
            "schema_version": 1,
            "type": "http_api",
            "config": {
                "method": "POST",
                "url": "https://api.example.com/appointments",
                "timeout_ms": 5000,
            },
        },
    )


async def _call(status_code: int, payload=None):
    with patch(
        "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
    ) as mock_client_class:
        client = AsyncMock()
        response = Mock()
        response.status_code = status_code
        response.json.return_value = payload if payload is not None else {"ok": True}
        client.request.return_value = response
        mock_client_class.return_value.__aenter__.return_value = client
        return await execute_http_tool(_tool(), {"name": "Asha"})


@pytest.mark.asyncio
class TestTheStatusCodeDecides:
    @pytest.mark.parametrize("code", [200, 201, 202, 204])
    async def test_a_2xx_is_a_success(self, code):
        assert (await _call(code))["status"] == "success"

    @pytest.mark.parametrize(
        "code",
        [400, 401, 403, 404, 409, 422, 429, 500, 502, 503],
        ids=lambda c: str(c),
    )
    async def test_every_error_response_is_reported_as_an_error(self, code):
        """401 and 404 are the two that matter most in practice — an expired
        token and a wrong datacentre in the URL — because both look like a
        working integration right up until a live call."""
        result = await _call(code)
        assert result["status"] == "error"
        assert result["status_code"] == code

    @pytest.mark.parametrize("code", [301, 302, 307, 308])
    async def test_a_redirect_is_not_a_success(self, code):
        """This client does not follow redirects, so the body is not the API's
        answer. The same 301 silently broke the exchange-rate feed for months;
        reported plainly, it gets the URL corrected instead."""
        assert (await _call(code))["status"] == "error"

    async def test_the_failure_still_carries_the_body(self):
        """The vendor's own error message is the fastest route to the cause, so
        it is passed through rather than swallowed by a generic string."""
        result = await _call(401, {"error": "INVALID_TOKEN"})
        assert result["data"]["error"] == "INVALID_TOKEN"
        assert "401" in result["error"]


@pytest.mark.asyncio
class TestWhatGoesInTheLog:
    async def test_request_values_are_not_logged(self):
        """The body is whatever the agent collected from the person on the line.
        Run rows carrying that data have to be erasable on request; a log line
        is not erasable at all, so the values never go in one.

        Captured through a loguru sink rather than caplog: this module logs via
        loguru, and bridging the two deadlocks the handler — a test that cannot
        fail is worse than no test.
        """
        from loguru import logger

        captured: list[str] = []
        sink_id = logger.add(captured.append, level="DEBUG")
        try:
            await _call(200)
        finally:
            logger.remove(sink_id)

        blob = "".join(captured)
        assert blob, "nothing was captured, so this proves nothing"
        assert "Asha" not in blob, f"a caller's name reached the logs: {blob}"
        # The field *names* are what makes a log useful for debugging, and they
        # identify nobody.
        assert "name" in blob
