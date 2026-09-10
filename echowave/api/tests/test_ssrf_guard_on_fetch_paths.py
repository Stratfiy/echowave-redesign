"""The pre-call fetch and the HTTP tool are server-side fetches of a URL the
customer chooses, running inside our network with a credential attached. Both
must refuse the private, loopback, link-local and cloud-metadata ranges, or
either is a request-forgery primitive pointed at our own infrastructure.

The guard itself (validate_user_configured_service_url) is covered elsewhere;
these tests prove the two paths actually call it and fail closed.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import api.services.pipecat.pre_call_fetch as pcf
import api.services.workflow.tools.custom_tool as custom_tool
import api.utils.url_security as url_security


@pytest.fixture
def saas(monkeypatch):
    # The guard is a no-op in OSS mode (localhost model servers are normal
    # there); the SaaS deployment is the one that must not be turned inward.
    monkeypatch.setattr(url_security, "DEPLOYMENT_MODE", "saas")


BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://127.0.0.1:8000/internal",  # loopback
    "http://localhost/admin",  # localhost by name
    "http://10.0.0.5/health",  # private
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", BLOCKED)
async def test_pre_call_fetch_refuses_internal_urls(saas, url):
    with patch.object(pcf.httpx, "AsyncClient") as client:
        result = await pcf.execute_pre_call_fetch(
            url=url,
            credential_uuid=None,
            call_context_vars={},
            workflow_id=1,
            organization_id=1,
        )
    assert result == {}
    client.assert_not_called()  # never left our network


@pytest.mark.asyncio
@pytest.mark.parametrize("url", BLOCKED)
async def test_custom_http_tool_refuses_internal_urls(saas, url):
    tool = SimpleNamespace(
        name="lookup",
        tool_uuid="t1",
        definition={"config": {"method": "GET", "url": url}},
    )
    with patch.object(custom_tool.httpx, "AsyncClient") as client:
        result = await custom_tool.execute_http_tool(
            tool=tool,
            arguments={},
            call_context_vars={},
            gathered_context_vars={},
            organization_id=1,
        )
    assert result["status"] == "error"
    client.assert_not_called()


@pytest.mark.asyncio
async def test_a_public_url_is_still_allowed(saas):
    tool = SimpleNamespace(
        name="lookup",
        tool_uuid="t1",
        definition={"config": {"method": "GET", "url": "https://api.example.com/x"}},
    )
    response = SimpleNamespace(
        status_code=200, json=lambda: {"ok": True}, text='{"ok": true}'
    )
    client = AsyncMock()
    client.__aenter__.return_value.request = AsyncMock(return_value=response)
    with patch.object(url_security, "_resolve_hostname_ips", return_value=[]):
        with patch.object(custom_tool.httpx, "AsyncClient", return_value=client):
            result = await custom_tool.execute_http_tool(
                tool=tool,
                arguments={},
                call_context_vars={},
                gathered_context_vars={},
                organization_id=1,
            )
    assert result["status"] == "success"
