"""The cloud-metadata service is refused whatever the deployment mode.

On the night the event webhook shipped, production accepted
``http://169.254.169.254/latest/meta-data/`` as a destination and answered
200. The gate existed and was applied; it returned early because
``DEPLOYMENT_MODE`` was never set on the box, so it defaulted to ``oss`` and
the whole check was skipped -- for webhooks, and for every pre-call fetch
and HTTP tool it had guarded for months.

The OSS carve-out was for a real need: a self-hoster pointing STT at
localhost or a LAN model server. It was never meant to permit the metadata
address, which no deployment has a legitimate reason to fetch on a
customer's behalf. So the ranges are split: link-local, multicast, reserved
and unspecified are refused in every mode; private, loopback and CGNAT only
in SaaS.

The first test below is the production failure, reproduced: the real route,
the real gate, the default mode, no patching.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import api.utils.url_security as url_security
from api.db import db_client
from api.utils.url_security import validate_user_configured_service_url


@pytest.fixture
def oss(monkeypatch):
    monkeypatch.setattr(url_security, "DEPLOYMENT_MODE", "oss")


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(url_security, "DEPLOYMENT_MODE", "saas")


class TestRefusedEverywhere:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://169.254.170.2/v2/credentials/",  # ECS task metadata
            "http://[fe80::1]/",
            "http://224.0.0.1/",
            "http://0.0.0.0/",
        ],
    )
    def test_in_oss_mode(self, oss, url):
        with pytest.raises(ValueError):
            validate_user_configured_service_url(url, field_name="URL")

    def test_a_name_that_resolves_to_metadata_is_refused_in_oss_mode(self, oss):
        """DNS is the way round an IP check, and in OSS mode nothing used to
        resolve the name at all."""
        with patch.object(
            socket,
            "getaddrinfo",
            return_value=[(None, None, None, None, ("169.254.169.254", 80))],
        ):
            with pytest.raises(ValueError):
                validate_user_configured_service_url(
                    "http://metadata.internal/", field_name="URL"
                )


class TestWhatOssStillAllows:
    """The reason the carve-out existed. Breaking it would be a different
    incident."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000/v1",
            "http://127.0.0.1:9000/",
            "http://10.0.0.5/health",
        ],
    )
    def test_localhost_and_lan_in_oss_mode(self, oss, url):
        validate_user_configured_service_url(url, field_name="stt.base_url")

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000/v1",
            "http://127.0.0.1:9000/",
            "http://10.0.0.5/health",
        ],
    )
    def test_and_the_same_are_refused_in_saas(self, saas, url):
        with pytest.raises(ValueError):
            validate_user_configured_service_url(url, field_name="stt.base_url")


@pytest.mark.asyncio
class TestTheProductionFailureReproduced:
    async def test_the_webhook_route_refuses_metadata_with_the_default_mode(self, oss):
        """The exact request production answered 200 to. Real route, real
        gate, nothing patched but the database."""
        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import (
            get_user,
            get_user_with_selected_organization,
        )

        who = SimpleNamespace(id=42, selected_organization_id=7)
        app.dependency_overrides[get_user] = lambda: who
        app.dependency_overrides[get_user_with_selected_organization] = lambda: who
        try:
            with (
                patch.object(
                    db_client,
                    "get_membership",
                    new=AsyncMock(return_value=SimpleNamespace(role="admin")),
                ),
                patch.object(
                    db_client,
                    "get_workflow",
                    new=AsyncMock(
                        return_value=SimpleNamespace(id=3, name="Front desk")
                    ),
                ),
                patch.object(
                    db_client, "save_bot_event_webhook", new=AsyncMock()
                ) as saved,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.put(
                        "/api/v1/workflows/3/event-webhook",
                        json={"url": "http://169.254.169.254/latest/meta-data/"},
                    )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 422, response.text
        saved.assert_not_awaited()


class TestABareHostnameInOss:
    """OSS accepts a bare host for a model server and always has. The
    tightening must not take that away -- and must still read the host off
    it, so the metadata address is not reachable by dropping the scheme."""

    def test_a_bare_public_host_is_still_accepted(self, oss):
        with patch.object(
            socket,
            "getaddrinfo",
            return_value=[(None, None, None, None, ("34.1.2.3", 443))],
        ):
            validate_user_configured_service_url(
                "api.elevenlabs.io", field_name="base_url"
            )

    def test_a_bare_metadata_address_is_still_refused(self, oss):
        with pytest.raises(ValueError):
            validate_user_configured_service_url(
                "169.254.169.254", field_name="base_url"
            )

    def test_saas_still_insists_on_a_scheme(self, saas):
        with pytest.raises(ValueError, match="must be an http"):
            validate_user_configured_service_url(
                "api.elevenlabs.io", field_name="base_url"
            )


class TestIpv6Loopback:
    """The CI runner resolves localhost to ::1 as well as 127.0.0.1, and
    Python files ::1 under is_reserved (all of ::/8 is). The first push of
    this refused it, and only on the runner -- this sandbox has no IPv6."""

    def test_accepted_in_oss_mode(self, oss):
        validate_user_configured_service_url(
            "http://[::1]:8443/", field_name="stt.base_url"
        )
        with patch.object(
            socket,
            "getaddrinfo",
            return_value=[
                (None, None, None, None, ("::1", 8443, 0, 0)),
                (None, None, None, None, ("127.0.0.1", 8443)),
            ],
        ):
            validate_user_configured_service_url(
                "https://localhost:8443", field_name="stt.base_url"
            )

    def test_still_refused_in_saas(self, saas):
        with pytest.raises(ValueError):
            validate_user_configured_service_url(
                "http://[::1]:8443/", field_name="stt.base_url"
            )

    def test_the_unspecified_address_is_still_refused_everywhere(self, oss):
        """Loopback is let past the reserved check; :: is not loopback."""
        with pytest.raises(ValueError):
            validate_user_configured_service_url(
                "http://[::]:8443/", field_name="stt.base_url"
            )
