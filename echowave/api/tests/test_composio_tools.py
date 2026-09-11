"""Composio tool execution: tenant scoping, failure shapes, and configuration.

The tests worth having here are not "does a POST get made". They are the three
ways this can be wrong in a way nobody notices until a customer does: a call
billed to the wrong organization's connected account, a tool that failed being
reported to the model as having worked, and a deployment with no key quietly
doing nothing instead of refusing.
"""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from api.schemas.tool import ComposioToolDefinition
from api.services.integrations.composio import client as composio_client
from api.services.workflow.pipecat_engine_custom_tools import _composio_timeout_secs


def _mock_response(status_code: int, json_body):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


def _patched_post(response):
    """Patch the module's AsyncClient and hand back the mock to assert on."""
    mock_client = AsyncMock()
    mock_client.post.return_value = response
    patcher = patch("api.services.integrations.composio.client.httpx.AsyncClient")
    mock_cls = patcher.start()
    mock_cls.return_value.__aenter__.return_value = mock_client
    return patcher, mock_client


class TestTenantScoping:
    """The only boundary between one customer's mailbox and another's agent."""

    def test_the_identifier_is_namespaced_and_derived_from_the_organization(self):
        assert composio_client.tenant_user_id(7) == "decibyl_org_7"
        assert composio_client.tenant_user_id(12345) == "decibyl_org_12345"

    def test_two_organizations_never_share_an_identifier(self):
        assert composio_client.tenant_user_id(1) != composio_client.tenant_user_id(2)

    @pytest.mark.parametrize("bad", [None, True, False, 0, -1, "7", 7.0, [7]])
    def test_anything_that_is_not_a_real_organization_id_is_refused(self, bad):
        """A missing or malformed id means we do not know whose call this is.

        Falling back to a default here would not fail loudly -- it would
        succeed, against somebody's account.
        """
        with pytest.raises(composio_client.ComposioNotConfigured):
            composio_client.tenant_user_id(bad)

    @pytest.mark.asyncio
    async def test_a_call_with_no_organization_never_reaches_the_network(self):
        patcher, mock_client = _patched_post(_mock_response(200, {"successful": True}))
        try:
            with pytest.raises(composio_client.ComposioNotConfigured):
                await composio_client.execute_tool(
                    tool_slug="GMAIL_SEND_EMAIL",
                    arguments={"to": "someone@example.com"},
                    organization_id=None,
                )
            mock_client.post.assert_not_called()
        finally:
            patcher.stop()


class TestExecution:
    @pytest.mark.asyncio
    async def test_the_organization_is_sent_as_the_user_id(self):
        patcher, mock_client = _patched_post(
            _mock_response(200, {"successful": True, "data": {"id": "msg-1"}})
        )
        try:
            with patch.object(composio_client, "COMPOSIO_API_KEY", "ak_test"):
                result = await composio_client.execute_tool(
                    tool_slug="GMAIL_SEND_EMAIL",
                    arguments={"recipient_email": "a@b.com"},
                    organization_id=42,
                )
        finally:
            patcher.stop()

        assert result == {"status": "success", "data": {"id": "msg-1"}}
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["user_id"] == "decibyl_org_42"
        assert kwargs["json"]["arguments"] == {"recipient_email": "a@b.com"}
        assert kwargs["headers"]["x-api-key"] == "ak_test"

    @pytest.mark.asyncio
    async def test_a_tool_that_failed_inside_a_200_is_reported_as_a_failure(self):
        """Composio returns HTTP 200 with ``successful: false`` when the
        provider rejected the call. Reading only the status code would tell the
        agent the email was sent, and the agent would tell the caller."""
        patcher, _ = _patched_post(
            _mock_response(
                200, {"successful": False, "error": "Recipient address rejected"}
            )
        )
        try:
            with patch.object(composio_client, "COMPOSIO_API_KEY", "ak_test"):
                result = await composio_client.execute_tool(
                    tool_slug="GMAIL_SEND_EMAIL",
                    arguments={},
                    organization_id=42,
                )
        finally:
            patcher.stop()

        assert result["status"] == "error"
        assert "Recipient address rejected" in result["error"]

    @pytest.mark.asyncio
    async def test_a_timeout_becomes_something_the_agent_can_say(self):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("too slow")
        with (
            patch(
                "api.services.integrations.composio.client.httpx.AsyncClient"
            ) as mock_cls,
            patch.object(composio_client, "COMPOSIO_API_KEY", "ak_test"),
        ):
            mock_cls.return_value.__aenter__.return_value = mock_client
            result = await composio_client.execute_tool(
                tool_slug="GMAIL_SEND_EMAIL", arguments={}, organization_id=42
            )

        assert result["status"] == "error"
        assert "in time" in result["error"]

    @pytest.mark.asyncio
    async def test_our_own_rejected_key_never_reaches_the_model(self):
        """A 401 is our billing problem. The model is one prompt injection away
        from repeating whatever we hand it, so it gets no detail at all."""
        patcher, _ = _patched_post(_mock_response(401, {"error": "invalid api key"}))
        try:
            with patch.object(composio_client, "COMPOSIO_API_KEY", "ak_wrong"):
                result = await composio_client.execute_tool(
                    tool_slug="GMAIL_SEND_EMAIL", arguments={}, organization_id=42
                )
        finally:
            patcher.stop()

        assert result["status"] == "error"
        assert "api key" not in result["error"].lower()
        assert "ak_wrong" not in result["error"]

    @pytest.mark.asyncio
    async def test_a_deployment_with_no_key_refuses_rather_than_no_ops(self):
        with patch.object(composio_client, "COMPOSIO_API_KEY", None):
            with pytest.raises(composio_client.ComposioNotConfigured):
                await composio_client.execute_tool(
                    tool_slug="GMAIL_SEND_EMAIL", arguments={}, organization_id=42
                )


class TestDefinition:
    def test_slugs_are_normalized_so_gmail_and_GMAIL_are_one_toolkit(self):
        definition = ComposioToolDefinition.model_validate(
            {
                "type": "composio",
                "config": {"toolkit": " gmail ", "tool_slug": "gmail_send_email"},
            }
        )
        assert definition.config.toolkit == "GMAIL"
        assert definition.config.tool_slug == "GMAIL_SEND_EMAIL"

    @pytest.mark.parametrize("bad", ["", "   ", "GM AIL", "gmail;drop", "gmail/send"])
    def test_a_slug_that_could_not_exist_is_refused_at_authoring_time(self, bad):
        """Better a create that fails than a tool that fails mid-conversation."""
        with pytest.raises(Exception):
            ComposioToolDefinition.model_validate(
                {"type": "composio", "config": {"toolkit": bad, "tool_slug": "X"}}
            )

    def test_the_shipped_timeout_is_short_enough_for_a_live_call(self):
        definition = ComposioToolDefinition.model_validate(
            {"type": "composio", "config": {"toolkit": "GMAIL", "tool_slug": "X"}}
        )
        assert definition.config.timeout_secs <= 15


class TestTimeoutResolution:
    @pytest.mark.parametrize(
        "config,expected",
        [
            ({}, 12.0),
            ({"timeout_secs": 3}, 3.0),
            ({"timeout_secs": 7.5}, 7.5),
            ({"timeout_secs": True}, 12.0),
            ({"timeout_secs": "5"}, 12.0),
            ({"timeout_secs": 0}, 12.0),
            ({"timeout_secs": -2}, 12.0),
            ({"timeout_secs": None}, 12.0),
        ],
    )
    def test_a_malformed_stored_timeout_falls_back_rather_than_raising(
        self, config, expected
    ):
        assert _composio_timeout_secs(config) == expected
