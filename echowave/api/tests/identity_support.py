"""Shared fixtures for the launch stream `identity` tests.

Real database (the conftest's), real services, fakes only at the edges:
Composio, the mail server, the push service and the carrier. Nothing here
calls a paid API or sends a real message.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from api import constants
from api.db import db_client
from api.enums import OrganizationRole


@dataclass
class Team:
    org: int
    other_org: int
    owner: Any  # admin of org
    member: Any  # plain member of org
    stranger: Any  # member of other_org only

    def as_user(self, user: Any, org: int | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            id=user.id,
            selected_organization_id=org or self.org,
            email=getattr(user, "email", None) or f"u{user.id}@example.test",
            email_verified_at="2026-10-01",
            provider_id=user.provider_id,
            is_superuser=False,
        )


async def make_team() -> Team:
    run = uuid4().hex[:8]
    owner, _ = await db_client.get_or_create_user_by_provider_id(f"id-owner-{run}")
    member, _ = await db_client.get_or_create_user_by_provider_id(f"id-member-{run}")
    stranger, _ = await db_client.get_or_create_user_by_provider_id(
        f"id-stranger-{run}"
    )
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"id-org-{run}", owner.id
    )
    other, _ = await db_client.get_or_create_organization_by_provider_id(
        f"id-other-{run}", stranger.id
    )
    await db_client.add_user_to_organization(
        owner.id, org.id, OrganizationRole.ADMIN.value
    )
    await db_client.add_user_to_organization(
        member.id, org.id, OrganizationRole.MEMBER.value
    )
    await db_client.add_user_to_organization(
        member.id, other.id, OrganizationRole.MEMBER.value
    )
    await db_client.add_user_to_organization(
        stranger.id, other.id, OrganizationRole.ADMIN.value
    )
    return Team(
        org=org.id, other_org=other.id, owner=owner, member=member, stranger=stranger
    )


@pytest.fixture
async def team(test_engine) -> Team:
    return await make_team()


@asynccontextmanager
async def client_as(user: SimpleNamespace):
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_user, None)


def flags(monkeypatch, *names: str) -> None:
    for name in names:
        monkeypatch.setattr(constants, name, True)


@dataclass
class FakeComposio:
    """Composio, as one dict of tenant -> accounts. Records every tenant it
    was asked about, so a test can prove a colleague's was never asked."""

    accounts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unavailable: bool = False
    delete_raises: Exception | None = None

    def add(
        self, org: int, user: int | None, toolkit: str, account_id: str, status="ACTIVE"
    ):
        tenant = f"decibyl_org_{org}" + (f"_user_{user}" if user else "")
        self.accounts.setdefault(tenant, []).append(
            {
                "connected_account_id": account_id,
                "app": toolkit,
                "label": f"{toolkit}-{account_id[-3:]}",
                "status": status,
                "connected_at": "2026-10-07T10:00:00Z",
            }
        )

    async def accounts_with_status(self, organization_id, *, user_id=None, **_):
        from api.services.integrations.composio import client

        tenant = client.tenant_user_id(organization_id, user_id)
        self.asked.append(tenant)
        if self.unavailable:
            raise client.ComposioUnavailable("down")
        return [dict(a) for a in self.accounts.get(tenant, [])]

    async def delete_connected_account(self, account_id, **_):
        if self.delete_raises is not None:
            raise self.delete_raises
        self.deleted.append(account_id)
        for accounts in self.accounts.values():
            accounts[:] = [
                a for a in accounts if a["connected_account_id"] != account_id
            ]

    async def connected_toolkits(self, organization_id, *, user_id=None, **_):
        from api.services.integrations.composio import client

        tenant = client.tenant_user_id(organization_id, user_id)
        return [
            a["app"].upper()
            for a in self.accounts.get(tenant, [])
            if a["status"] == "ACTIVE"
        ]


@pytest.fixture
def composio(monkeypatch):
    from api.services.integrations.composio import client

    fake = FakeComposio()
    monkeypatch.setattr(client, "is_configured", lambda: True)
    monkeypatch.setattr(client, "accounts_with_status", fake.accounts_with_status)
    monkeypatch.setattr(
        client, "delete_connected_account", fake.delete_connected_account
    )
    monkeypatch.setattr(client, "connected_toolkits", fake.connected_toolkits)
    monkeypatch.setattr(
        client, "toolkit_name", AsyncMock(side_effect=lambda slug, **_: slug.title())
    )
    monkeypatch.setattr(
        client,
        "connect_link",
        AsyncMock(
            return_value={"url": "https://connect.example/abc", "expires_at": None}
        ),
    )
    return fake


@pytest.fixture
def no_queue():
    """Confirm queues the run; the test runs it by hand."""
    with patch("api.tasks.arq.enqueue_job", AsyncMock()) as enqueue:
        yield enqueue
