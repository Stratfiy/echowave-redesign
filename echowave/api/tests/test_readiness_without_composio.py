"""Readiness answers on a deployment with no Composio key.

Found by walking every customer screen against a running stack: the
readiness card on a bot's settings tab returned 500, and the card exists
precisely to tell an operator when a connector is broken. It failed in
exactly the case it was built for.

The cause was an asymmetry in ``connected_toolkits``. Every failure during
the request -- a timeout, a 500 from Composio, unparseable JSON -- already
returned "nothing is connected". The one failure *before* the request, an
absent API key, raised instead, and nothing up the stack caught it.
"""

import pytest

from api.services.integrations.composio import client as composio


class TestListingConnectionsWithoutAKey:
    async def test_reports_nothing_connected_rather_than_raising(self, monkeypatch):
        def no_key():
            raise composio.ComposioNotConfigured("COMPOSIO_API_KEY is not set")

        monkeypatch.setattr(composio, "_headers", no_key)
        assert await composio.connected_toolkits(1) == []

    async def test_the_same_is_true_with_no_organization(self, monkeypatch):
        def no_key():
            raise composio.ComposioNotConfigured("COMPOSIO_API_KEY is not set")

        monkeypatch.setattr(composio, "_headers", no_key)
        assert await composio.connected_toolkits(None) == []

    async def test_execution_still_refuses_loudly(self, monkeypatch):
        # An empty list is a truthful answer to "what is connected" and a
        # lie in answer to "run this tool". The two call sites that catch
        # ComposioNotConfigured are both on the execution path, and this
        # keeps it reaching them.
        def no_key(*args, **kwargs):
            raise composio.ComposioNotConfigured("COMPOSIO_API_KEY is not set")

        monkeypatch.setattr(composio, "_headers", no_key)
        with pytest.raises(composio.ComposioNotConfigured):
            await composio.execute_tool(
                tool_slug="GMAIL_SEND_EMAIL", arguments={}, organization_id=1
            )
