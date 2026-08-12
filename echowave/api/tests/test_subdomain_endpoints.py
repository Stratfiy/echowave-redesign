"""What a split-hostname install publishes about itself, and who owns /api/.

Both halves of this file pin bugs that shipped to production and were invisible
from the server side: the API was healthy, nginx was serving, every container
was up, and the app was unusable.

1. nginx on the app host proxied the whole ``/api/`` prefix to FastAPI. The UI
   is a Next.js app that serves its own routes under that prefix — including
   the login flow — so those requests reached a backend that has never heard of
   them and got ``{"detail":"Not Found"}``.

2. ``BACKEND_API_ENDPOINT`` fell back to ``PUBLIC_BASE_URL``, the root host,
   which on a split install serves a 404 pointer and nothing else. That value is
   what ``/health`` reports to the browser and what webhooks and embed snippets
   are built from, so all of them pointed at a dead address.

Neither failure raises anywhere. They are only observable from outside.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ECHOWAVE_DIR = Path(__file__).resolve().parents[2]
SUBDOMAIN_TEMPLATE = ECHOWAVE_DIR / "deploy/templates/nginx.subdomains.conf.template"
UI_API_ROUTES_DIR = ECHOWAVE_DIR / "ui/src/app/api"


def _server_blocks(config: str) -> list[str]:
    """Split an nginx config into its top-level ``server { ... }`` blocks."""
    blocks: list[str] = []
    depth = 0
    start = None
    for i, char in enumerate(config):
        if char == "{":
            if depth == 0:
                opener = config.rfind("server", 0, i)
                if opener != -1 and config[opener:i].strip() == "server":
                    start = opener
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                blocks.append(config[start : i + 1])
                start = None
    return blocks


def _block_for(server_name: str) -> str:
    config = SUBDOMAIN_TEMPLATE.read_text()
    for block in _server_blocks(config):
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("server_name ") and stripped.rstrip(";").split()[
                1:
            ] == [server_name]:
                return block
    raise AssertionError(f"no server block with server_name exactly {server_name}")


def _proxied_locations(block: str) -> list[str]:
    """Prefix locations in a block that proxy to the API upstream."""
    proxied: list[str] = []
    current: str | None = None
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("location ") and stripped.endswith("{"):
            parts = stripped.split()
            current = parts[1] if len(parts) >= 3 else None
        elif "proxy_pass" in stripped and "decibyl_api" in stripped and current:
            proxied.append(current)
            current = None
    return proxied


class TestTheAppHostDoesNotSwallowTheUIsOwnRoutes:
    """The bug that put a "Backend connection failed" banner over the login form.

    Next.js owns ``/api/auth/*`` and ``/api/config/*`` on the app host. FastAPI
    owns ``/api/v1/``. A ``location /api/`` on the app host gives FastAPI both,
    and the half it does not own is the half that logs users in.
    """

    def test_the_app_host_proxies_only_the_versioned_api(self):
        assert _proxied_locations(_block_for("__DECIBYL_APP_HOST__")) == ["/api/v1/"]

    def test_the_mcp_contract_is_still_served_here(self):
        """The reason the app host proxies any API at all.

        ``https://app.<domain>/api/v1/mcp/`` is published in the docs and saved
        in customers' MCP configs. Narrowing to /api/v1/ has to keep it.
        """
        assert "/api/v1/mcp/".startswith(
            _proxied_locations(_block_for("__DECIBYL_APP_HOST__"))[0]
        )

    def test_every_next_route_the_repo_actually_has_still_reaches_next(self):
        """Ties the config to the routes on disk, so a new one that collides
        with the proxied prefix fails here instead of in production."""
        app_prefix = _proxied_locations(_block_for("__DECIBYL_APP_HOST__"))[0]

        routes = sorted(
            "/api/" + str(p.parent.relative_to(UI_API_ROUTES_DIR)).replace("\\", "/")
            for p in UI_API_ROUTES_DIR.rglob("route.ts")
        )
        assert routes, "expected to find Next.js API routes; did the tree move?"

        stolen = [
            r for r in routes if r.startswith(app_prefix) and not r.startswith("/api/v1")
        ]
        assert not stolen, f"nginx sends these Next.js routes to FastAPI: {stolen}"

    def test_the_api_host_still_proxies_everything(self):
        """No Next.js on the API host, so the wide prefix is correct there."""
        assert _proxied_locations(_block_for("__DECIBYL_API_HOST__")) == ["/api/"]


class TestTheApiHostIsWhatGetsPublished:
    """``PUBLIC_BASE_URL`` is where the deployment is rooted, not where the API
    answers. On a split install the root host serves a 404 pointer."""

    @pytest.fixture
    def constants(self, monkeypatch):
        """Reload api.constants under a given environment, then put it back."""

        def load(**env: str | None):
            for key in (
                "DECIBYL_API_HOST",
                "BACKEND_API_ENDPOINT",
                "MINIO_PUBLIC_ENDPOINT",
                "PUBLIC_BASE_URL",
            ):
                monkeypatch.delenv(key, raising=False)
            for key, value in env.items():
                if value is not None:
                    monkeypatch.setenv(key, value)
            import api.constants as module

            return importlib.reload(module)

        yield load

        monkeypatch.undo()
        import api.constants as module

        importlib.reload(module)

    def test_the_backend_endpoint_follows_the_api_host(self, constants):
        module = constants(
            DECIBYL_API_HOST="api.example.com",
            PUBLIC_BASE_URL="https://example.com",
        )
        assert module.BACKEND_API_ENDPOINT == "https://api.example.com"

    def test_recordings_follow_the_api_host_too(self, constants):
        """/voice-audio/ is proxied from the API host alone."""
        module = constants(
            DECIBYL_API_HOST="api.example.com",
            PUBLIC_BASE_URL="https://example.com",
        )
        assert module.MINIO_PUBLIC_ENDPOINT == "https://api.example.com"

    def test_an_explicit_override_still_wins(self, constants):
        """A split deployment pointing object storage or the API elsewhere."""
        module = constants(
            DECIBYL_API_HOST="api.example.com",
            BACKEND_API_ENDPOINT="https://elsewhere.example.net",
            MINIO_PUBLIC_ENDPOINT="https://cdn.example.net",
        )
        assert module.BACKEND_API_ENDPOINT == "https://elsewhere.example.net"
        assert module.MINIO_PUBLIC_ENDPOINT == "https://cdn.example.net"

    def test_a_single_host_install_is_unchanged(self, constants):
        """No DECIBYL_API_HOST means one name for everything, as before."""
        module = constants(PUBLIC_BASE_URL="https://example.com")
        assert module.BACKEND_API_ENDPOINT == "https://example.com"
        assert module.MINIO_PUBLIC_ENDPOINT == "https://example.com"

    def test_local_dev_still_falls_back_to_localhost(self, constants):
        module = constants()
        assert module.BACKEND_API_ENDPOINT == "http://localhost:8000"
        assert module.MINIO_PUBLIC_ENDPOINT == "http://localhost:9000"
