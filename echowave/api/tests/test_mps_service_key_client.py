import pytest

from api.services.mps_service_key_client import MPSServiceKeyClient


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.request = object()

    def json(self):
        return self._payload


def test_validate_service_key_uses_bearer_self_usage(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers):
            calls.append(("GET", url, headers))
            return _Response(200)

    monkeypatch.setattr("api.services.mps_service_key_client.httpx.Client", FakeClient)

    client = MPSServiceKeyClient()

    assert client.validate_service_key("mps_sk_paid") is True
    assert calls == [
        (
            "GET",
            f"{client.base_url}/api/v1/service-keys/usage/self",
            {
                "Authorization": "Bearer mps_sk_paid",
                "Content-Type": "application/json",
            },
        )
    ]


@pytest.mark.asyncio
async def test_create_correlation_id_uses_bearer_auth(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            calls.append(("POST", url, json, headers))
            return _Response(200, {"correlation_id": "mps-corr-123"})

    monkeypatch.setattr(
        "api.services.mps_service_key_client.httpx.AsyncClient", FakeAsyncClient
    )

    client = MPSServiceKeyClient()

    assert await client.create_correlation_id(
        service_key="mps_sk_paid",
        workflow_run_id=42,
    ) == {"correlation_id": "mps-corr-123"}
    assert calls == [
        (
            "POST",
            f"{client.base_url}/api/v1/service-keys/correlation-id/self",
            {"workflow_run_id": 42},
            {
                "Authorization": "Bearer mps_sk_paid",
                "Content-Type": "application/json",
            },
        )
    ]


class TestMpsBeingUnreachable:
    """A managed proxy that was never deployed must not break a page.

    The default MPS_API_URL points at a host that does not resolve, and a
    self-hosted install using its own provider keys has no use for MPS at all.
    Listing keys used to raise the transport error, which the route turned into
    a 500 -- so the screen failed for exactly the deployments MPS never served.

    Non-200 from MPS already meant "no keys". Unreachable means the same thing:
    there are none to report either way.
    """

    async def test_a_dns_failure_reports_no_keys(self, monkeypatch):
        import httpx

        from api.services.mps_service_key_client import MPSServiceKeyClient

        class _Failing:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, *a, **kw):
                raise httpx.ConnectError("[Errno -2] Name or service not known")

        monkeypatch.setattr(
            "api.services.mps_service_key_client.httpx.AsyncClient", _Failing
        )

        assert await MPSServiceKeyClient().get_service_keys(organization_id=1) == []

    async def test_a_timeout_reports_no_keys(self, monkeypatch):
        import httpx

        from api.services.mps_service_key_client import MPSServiceKeyClient

        class _Timing:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, *a, **kw):
                raise httpx.ReadTimeout("too slow")

        monkeypatch.setattr(
            "api.services.mps_service_key_client.httpx.AsyncClient", _Timing
        )

        assert await MPSServiceKeyClient().get_service_keys(created_by="u") == []
